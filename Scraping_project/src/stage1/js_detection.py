"""Advanced JavaScript Detection Heuristics.

Detects single-page applications (SPAs) and JavaScript-heavy pages that
require rendering with Playwright/Puppeteer.
"""

import logging
import re
from typing import Dict, List

from scrapy.http import Response

logger = logging.getLogger(__name__)


class JSDetector:
    """Advanced JavaScript requirement detection."""

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
        """Initialize detector with response.

        Args:
            response: Scrapy response object
        """
        self.response = response
        self.html = response.text
        self.html_lower = response.text.lower()
        self.url = response.url

    def requires_js_rendering(self) -> Dict[str, any]:
        """Determine if page requires JavaScript rendering.

        Returns:
            Dictionary with detection results:
            {
                'requires_js': bool,
                'confidence': float (0.0-1.0),
                'reasons': List[str],
                'detected_framework': str or None,
            }
        """
        reasons = []
        confidence = 0.0
        detected_framework = None

        # 1. Check for SPA frameworks
        framework_result = self._detect_spa_framework()
        if framework_result['detected']:
            confidence += 0.4
            detected_framework = framework_result['framework']
            reasons.append(f"Detected {framework_result['framework']} framework")

        # 2. Check for bundled application code
        bundled_result = self._detect_bundled_app()
        if bundled_result['detected']:
            confidence += 0.3
            reasons.append(f"Found bundled app: {bundled_result['files']}")

        # 3. Check for state hydration
        state_result = self._detect_state_objects()
        if state_result['detected']:
            confidence += 0.2
            reasons.append(f"Found state object: {state_result['objects']}")

        # 4. Check for heavy async loading
        async_result = self._detect_async_loading()
        if async_result['heavy']:
            confidence += 0.2
            reasons.append(f"Heavy async loading: {async_result['count']} indicators")

        # 5. Check for minimal initial content
        content_result = self._check_minimal_content()
        if content_result['minimal']:
            confidence += 0.3
            reasons.append(
                f"Minimal initial content: {content_result['text_length']} chars"
            )

        # 6. Check for empty body with scripts
        empty_result = self._check_empty_body()
        if empty_result['empty']:
            confidence += 0.4
            reasons.append("Empty body with script tags (classic SPA)")

        # Cap confidence at 1.0
        confidence = min(confidence, 1.0)

        # Determine if rendering required (confidence > 0.5)
        requires_js = confidence > 0.5

        return {
            'requires_js': requires_js,
            'confidence': confidence,
            'reasons': reasons,
            'detected_framework': detected_framework,
        }

    def _detect_spa_framework(self) -> Dict[str, any]:
        """Detect SPA framework indicators.

        Returns:
            {'detected': bool, 'framework': str or None}
        """
        for framework, indicators in self.SPA_FRAMEWORKS.items():
            matches = sum(1 for ind in indicators if ind.lower() in self.html_lower)
            if matches >= 2:  # Require at least 2 indicators
                return {'detected': True, 'framework': framework}

        return {'detected': False, 'framework': None}

    def _detect_bundled_app(self) -> Dict[str, any]:
        """Detect bundled application files (webpack, rollup, etc).

        Returns:
            {'detected': bool, 'files': List[str]}
        """
        # Extract script src attributes
        script_srcs = self.response.css('script::attr(src)').getall()

        bundled_files = []
        for src in script_srcs:
            for pattern in self.BUNDLED_APP_PATTERNS:
                if pattern.search(src):
                    bundled_files.append(src)
                    break

        detected = len(bundled_files) > 0

        return {
            'detected': detected,
            'files': bundled_files[:3],  # Return first 3
        }

    def _detect_state_objects(self) -> Dict[str, any]:
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

    def _detect_async_loading(self) -> Dict[str, any]:
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

    def _check_minimal_content(self) -> Dict[str, any]:
        """Check if page has minimal initial text content.

        Returns:
            {'minimal': bool, 'text_length': int}
        """
        # Extract visible text
        text_content = self.response.css('body ::text').getall()
        total_text = ''.join(text_content).strip()
        text_length = len(total_text)

        # Check if there are script tags
        has_scripts = '<script' in self.html_lower

        # Minimal if less than 200 chars and has scripts
        minimal = text_length < 200 and has_scripts

        return {
            'minimal': minimal,
            'text_length': text_length,
        }

    def _check_empty_body(self) -> Dict[str, any]:
        """Check for empty body with only div/script (classic SPA pattern).

        Returns:
            {'empty': bool}
        """
        # Look for body with just a root div and scripts
        body = self.response.css('body').get()

        if not body:
            return {'empty': False}

        # Remove script and style tags
        body_text = re.sub(r'<script[^>]*>.*?</script>', '', body, flags=re.DOTALL | re.IGNORECASE)
        body_text = re.sub(r'<style[^>]*>.*?</style>', '', body_text, flags=re.DOTALL | re.IGNORECASE)

        # Remove HTML tags and check remaining text
        body_text = re.sub(r'<[^>]+>', '', body_text)
        body_text = body_text.strip()

        # Empty if less than 100 chars
        empty = len(body_text) < 100

        return {'empty': empty}

    def get_spa_root_selector(self) -> str or None:
        """Get CSS selector for SPA root element.

        Useful for waiting for content to load in Playwright.

        Returns:
            CSS selector or None
        """
        # Common SPA root selectors
        root_ids = ['root', 'app', '__next', '__nuxt', 'main']

        for root_id in root_ids:
            if f'id="{root_id}"' in self.html or f"id='{root_id}'" in self.html:
                return f'#{root_id}'

        # Check for common class names
        root_classes = ['app', 'application', 'spa-root', 'root']

        for root_class in root_classes:
            if f'class="{root_class}"' in self.html or f"class='{root_class}'" in self.html:
                return f'.{root_class}'

        return None


def detect_js_requirement(response: Response) -> bool:
    """Simple boolean check if page requires JS rendering.

    Args:
        response: Scrapy response

    Returns:
        True if JS rendering required
    """
    detector = JSDetector(response)
    result = detector.requires_js_rendering()
    return result['requires_js']


def detect_js_with_details(response: Response) -> Dict[str, any]:
    """Detailed JS requirement detection.

    Args:
        response: Scrapy response

    Returns:
        Full detection results dictionary
    """
    detector = JSDetector(response)
    return detector.requires_js_rendering()
