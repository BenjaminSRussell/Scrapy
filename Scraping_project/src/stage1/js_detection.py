"""Detect whether a page needs JavaScript rendering."""

import logging
import re

from scrapy.http import Response

logger = logging.getLogger(__name__)


class JSDetector:
    """Heuristics for JavaScript-heavy pages."""

    # Confidence score constants (named for readability and easy tuning)
    FRAMEWORK_CONFIDENCE = 0.4
    BUNDLED_APP_CONFIDENCE = 0.3
    STATE_OBJECT_CONFIDENCE = 0.2
    ASYNC_LOADING_CONFIDENCE = 0.2
    MINIMAL_CONTENT_CONFIDENCE = 0.3
    EMPTY_BODY_CONFIDENCE = 0.4
    CONFIDENCE_THRESHOLD = 0.5

    # Pre-compiled regex patterns (compiled once for performance)
    TAG_STRIP_PATTERN = re.compile(r'<[^>]+>')
    SCRIPT_STRIP_PATTERN = re.compile(r'<script[^>]*>.*?</script>', flags=re.DOTALL | re.IGNORECASE)
    STYLE_STRIP_PATTERN = re.compile(r'<style[^>]*>.*?</style>', flags=re.DOTALL | re.IGNORECASE)

    # SPA Framework indicators
    SPA_FRAMEWORKS = {
        'react': [
            'react.js', 'react-dom', '__REACT', '_reactRoot',
            'data-reactroot', 'data-react-helmet', 'react-app',
        ],
        'vue': [
            'vue.js', 'vuejs', '__VUE__', 'v-app', 'v-cloak',
            'data-v-', '[data-v-',
        ],
        'angular': [
            'angular.js', 'ng-app', 'ng-controller', 'ng-view',
            '__ANGULAR', '[ng-', 'data-ng-',
        ],
        'svelte': [
            'svelte', '__SVELTE__', 'svelte-', 'class="svelte-',
        ],
        'next.js': [
            '__next', '__NEXT_DATA__', '_next/static', 'next.js',
            '<div id="__next">', 'data-nextjs',
        ],
        'nuxt': [
            '__nuxt', '__NUXT__', 'nuxt.js', '<div id="__nuxt">',
        ],
        'ember': [
            'ember.js', 'ember-application', 'data-ember-',
        ],
    }

    # Indicators of heavy async loading
    ASYNC_INDICATORS = [
        'fetch(', 'axios', '$.ajax', '$.get', '$.post',
        'XMLHttpRequest', 'new Request(', '.then(', 'async function',
        'await ', 'Promise.all', 'Observable',
    ]

    # Bundled application indicators
    BUNDLED_APP_PATTERNS = [
        re.compile(r'app\.[a-f0-9]{8,}\.js'),  # app.12345678.js
        re.compile(r'bundle\.[a-f0-9]{8,}\.js'),  # bundle.12345678.js
        re.compile(r'chunk\.[a-f0-9]{8,}\.js'),  # chunk.12345678.js
        re.compile(r'vendor\.[a-f0-9]{8,}\.js'),  # vendor.12345678.js
        re.compile(r'main\.[a-f0-9]{8,}\.js'),  # main.12345678.js
        re.compile(r'runtime\.[a-f0-9]{8,}\.js'),  # runtime.12345678.js
    ]

    # JSON state objects (client-side hydration)
    STATE_OBJECT_PATTERNS = [
        'window.__INITIAL_STATE__',
        'window.__PRELOADED_STATE__',
        'window.__DATA__',
        'window.__APOLLO_STATE__',
        'window.__REDUX_STATE__',
        'window.__STORE__',
    ]

    def __init__(self, response: Response):
        """Cache response data for repeated checks."""
        self.response = response
        self.html = response.text
        self.html_lower = response.text.lower()
        self.url = response.url

    def requires_js_rendering(self) -> dict[str, any]:
        """Return detection verdict, confidence, and supporting reasons."""
        reasons = []
        confidence = 0.0
        detected_framework = None

        # SPA frameworks
        framework_result = self._detect_spa_framework()
        if framework_result['detected']:
            confidence += self.FRAMEWORK_CONFIDENCE
            detected_framework = framework_result['framework']
            reasons.append(f"Detected {framework_result['framework']} framework")

        # Bundled application code
        bundled_result = self._detect_bundled_app()
        if bundled_result['detected']:
            confidence += self.BUNDLED_APP_CONFIDENCE
            reasons.append(f"Found bundled app: {bundled_result['files']}")

        # State hydration
        state_result = self._detect_state_objects()
        if state_result['detected']:
            confidence += self.STATE_OBJECT_CONFIDENCE
            reasons.append(f"Found state object: {state_result['objects']}")

        # Heavy async loading
        async_result = self._detect_async_loading()
        if async_result['heavy']:
            confidence += self.ASYNC_LOADING_CONFIDENCE
            reasons.append(f"Heavy async loading: {async_result['count']} indicators")

        # Minimal initial content
        content_result = self._check_minimal_content()
        if content_result['minimal']:
            confidence += self.MINIMAL_CONTENT_CONFIDENCE
            reasons.append(
                f"Minimal initial content: {content_result['text_length']} chars"
            )

        # Empty body with scripts
        empty_result = self._check_empty_body()
        if empty_result['empty']:
            confidence += self.EMPTY_BODY_CONFIDENCE
            reasons.append("Empty body with script tags (classic SPA)")

        # Cap confidence at 1.0
        confidence = min(confidence, 1.0)

        # Determine if rendering required (using named threshold constant)
        requires_js = confidence > self.CONFIDENCE_THRESHOLD

        return {
            'requires_js': requires_js,
            'confidence': confidence,
            'reasons': reasons,
            'detected_framework': detected_framework,
        }

    def _detect_spa_framework(self) -> dict[str, any]:
        """Return framework detection info based on SPA indicators."""
        for framework, indicators in self.SPA_FRAMEWORKS.items():
            matches = sum(1 for ind in indicators if ind.lower() in self.html_lower)
            if matches >= 2:  # Require at least 2 indicators
                return {'detected': True, 'framework': framework}

        return {'detected': False, 'framework': None}

    def _detect_bundled_app(self) -> dict[str, any]:
        """Return bundle detection info based on script filenames."""
        script_srcs = self.response.css('script::attr(src)').getall()

        bundled_files = []
        for src in script_srcs:
            for pattern in self.BUNDLED_APP_PATTERNS:
                if pattern.search(src):
                    bundled_files.append(src)
                    break

        detected = len(bundled_files) > 0

        return {'detected': detected, 'files': bundled_files[:3]}

    def _detect_state_objects(self) -> dict[str, any]:
        """Detect client-side state hydration objects.

        Returns:
            {'detected': bool, 'objects': List[str]}
        """
        found_objects = []

        for pattern in self.STATE_OBJECT_PATTERNS:
            if pattern in self.html:
                found_objects.append(pattern)

        detected = len(found_objects) > 0

        return {
            'detected': detected,
            'objects': found_objects,
        }

    def _detect_async_loading(self) -> dict[str, any]:
        """Detect heavy async loading patterns.

        Returns:
            {'heavy': bool, 'count': int}
        """
        count = 0

        for indicator in self.ASYNC_INDICATORS:
            count += self.html_lower.count(indicator.lower())

        # Consider "heavy" if more than 5 async indicators
        heavy = count > 5

        return {
            'heavy': heavy,
            'count': count,
        }

    def _check_minimal_content(self) -> dict[str, any]:
        """Return text-length info for fast-loading placeholder pages."""
        text_content = self.response.css('body ::text').getall()
        total_text = ''.join(text_content).strip()
        text_length = len(total_text)

        has_scripts = '<script' in self.html_lower

        minimal = text_length < 200 and has_scripts

        return {
            'minimal': minimal,
            'text_length': text_length,
        }

    def _check_empty_body(self) -> dict[str, any]:
        """Return whether the body is mostly empty aside from scripts."""
        body = self.response.css('body').get()

        if not body:
            return {'empty': False}

        body_text = self.SCRIPT_STRIP_PATTERN.sub('', body)
        body_text = self.STYLE_STRIP_PATTERN.sub('', body_text)

        body_text = self.TAG_STRIP_PATTERN.sub('', body_text)
        body_text = body_text.strip()

        empty = len(body_text) < 100

        return {'empty': empty}

    def get_spa_root_selector(self) -> str or None:
        """Return SPA root selector used by Playwright waits."""
        root_ids = ['root', 'app', '__next', '__nuxt', 'main']

        for root_id in root_ids:
            if f'id="{root_id}"' in self.html or f"id='{root_id}'" in self.html:
                return f'#{root_id}'

        root_classes = ['app', 'application', 'spa-root', 'root']

        for root_class in root_classes:
            if f'class="{root_class}"' in self.html or f"class='{root_class}'" in self.html:
                return f'.{root_class}'

        return None


def detect_js_requirement(response: Response) -> bool:
    """Return True when the page should be JS-rendered."""
    detector = JSDetector(response)
    result = detector.requires_js_rendering()
    return result['requires_js']


def detect_js_with_details(response: Response) -> dict[str, any]:
    """Return the full detection payload with reasons and confidence."""
    detector = JSDetector(response)
    return detector.requires_js_rendering()
