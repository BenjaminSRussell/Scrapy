import csv
import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Tuple, AsyncGenerator
from urllib.parse import urlparse

import scrapy
from scrapy.linkextractors import LinkExtractor
from scrapy.http import Response

try:
    from scrapy_playwright.page import Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Page = None  # type: ignore

from src.common.schemas import DiscoveryItem
from src.common.urls import canonicalize_url_simple
from src.common.storage import URLCache, PaginationCache
from src.common.feedback import FeedbackStore
from src.common.adaptive_depth import AdaptiveDepthManager
from src.common.logging import get_structured_logger, set_session_id, set_trace_id, clear_trace_context


logger = get_structured_logger(__name__, component="discovery_spider", stage="stage1")


DYNAMIC_SCRIPT_HINTS = (
    'fetch(', 'xmlhttprequest', 'axios', '$.get', '$.post', '.ajax', 'loadmore',
    'nexturl', 'next_url', 'load_more', 'apiurl', 'api_url', 'dispatch('
)

SCRIPT_URL_PATTERN = re.compile(
    r'["\\](?P<url>(?:https?:)?//[\w\.-]+(?:/[\w\./\?-]*)?|[\w\./\?-]+)["\\]',
    re.IGNORECASE
)

JSON_URL_KEY_HINTS = {'url', 'href', 'link', 'endpoint', 'action', 'download'}

DATA_ATTRIBUTE_CANDIDATES = (
    'data-url', 'data-src', 'data-endpoint', 'data-load', 'data-load-url',
    'data-href', 'data-link', 'data-api', 'data-request-url', 'data-action',
    'data-next', 'data-feed', 'data-bg', 'data-background', 'data-image',
    'data-video', 'data-iframe', 'data-target'
)


class DiscoverySpider(scrapy.Spider):
    """Stage 1 Discovery Spider - finds and catalogs new URLs"""

    name = "discovery"

    @staticmethod
    def _as_iterable(value):
        """
        Coerce config inputs to iterables, handling Mocks, strings, and lists.

        Args:
            value: Input value that might be a Mock, string, list, or other type

        Returns:
            list: A list representation of the input value
        """
        if value is None:
            return []

        # Handle Mock objects (for testing)
        from unittest.mock import Mock
        if isinstance(value, Mock):
            # Check if it has a return_value that's a list
            if hasattr(value, 'return_value') and isinstance(value.return_value, list):
                return value.return_value
            # Otherwise treat as single item
            return [str(value)]

        # Handle strings (split by comma if present)
        if isinstance(value, str):
            if ',' in value:
                return [v.strip() for v in value.split(',') if v.strip()]
            return [value] if value else []

        # Handle iterables (but not str/bytes)
        if hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
            try:
                return list(value)
            except (TypeError, ValueError):
                return [str(value)]

        # Fallback: treat as single item
        return [str(value)]

    def __init__(self, max_depth: int = 3, allowed_domains: list = None, settings: dict = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Import Settings class properly
        from scrapy.settings import Settings
        from unittest.mock import Mock

        # Ensure self.settings is always set (might be set by Scrapy's from_crawler)
        if not hasattr(self, 'settings'):
            if settings and not isinstance(settings, Settings):
                # Don't try to wrap Mock objects in Settings
                if isinstance(settings, Mock):
                    self.settings = settings
                else:
                    self.settings = Settings(settings)
            elif settings:
                self.settings = settings
            else:
                # Create default Settings if none provided
                self.settings = Settings()

        self.max_depth = int(max_depth)

        # Set session ID for this crawl run
        self.session_id = set_session_id()

        # Load allowed domains from configuration or use default
        self.allowed_domains = self._as_iterable(allowed_domains) or ["uconn.edu"]

        logger.log_with_context(
            logging.INFO,
            "Allowed domains configured",
            allowed_domains=self.allowed_domains,
            domain_count=len(self.allowed_domains)
        )

        # Get seed file and output file from settings
        self.seed_file = self.settings.get('SEED_FILE', 'data/raw/uconn_urls.csv')
        self.output_file = self.settings.get('STAGE1_OUTPUT_FILE', 'data/processed/stage01/discovery_output.jsonl')

        # Initialize persistent deduplication if enabled
        use_persistent_dedup = self.settings.getbool('USE_PERSISTENT_DEDUP', True)
        dedup_cache_path = self.settings.get('DEDUP_CACHE_PATH', 'data/cache/url_cache.db')

        if use_persistent_dedup:
            self.url_cache = URLCache(Path(dedup_cache_path))
            logger.log_with_context(
                logging.INFO,
                "Using persistent deduplication with SQLite",
                cache_path=str(dedup_cache_path),
                dedup_type="persistent"
            )
            # Load existing hashes for in-memory fallback
            self.url_hashes = self.url_cache.get_all_hashes()
            logger.log_with_context(
                logging.INFO,
                "Loaded existing URL hashes from cache",
                hash_count=len(self.url_hashes)
            )
        else:
            self.url_cache = None
            self.url_hashes = set()
            logger.log_with_context(
                logging.INFO,
                "Using in-memory deduplication",
                dedup_type="in_memory"
            )

        # Initialize pagination cache
        pagination_cache_path = self.settings.get('PAGINATION_CACHE_PATH', 'data/cache/pagination_cache.db')
        self.pagination_cache = PaginationCache(Path(pagination_cache_path))

        # Initialize feedback store for adaptive discovery
        feedback_file = Path("data/feedback/stage2_feedback.json")
        self.feedback_store = FeedbackStore(feedback_file)
        logger.info(f"Loaded feedback from Stage 2 for adaptive discovery")

        # Check which sources should be throttled based on feedback
        self.throttled_sources = set()
        for source in ['ajax_endpoint', 'json_blob', 'pagination', 'data_attribute', 'form_action', 'meta_refresh']:
            if self.feedback_store.should_throttle_source(source, min_samples=50, max_success_rate=0.3):
                self.throttled_sources.add(source)
                logger.log_with_context(
                    logging.WARNING,
                    "Discovery source will be throttled due to low success rate",
                    source=source,
                    reason="low_success_rate"
                )

        # Get low-quality URL patterns to avoid
        self.low_quality_patterns = set(self.feedback_store.get_low_quality_patterns(min_samples=10, max_success_rate=0.3))
        if self.low_quality_patterns:
            logger.info(f"Avoiding {len(self.low_quality_patterns)} low-quality URL patterns from previous crawls")

        # Initialize adaptive depth manager
        adaptive_depth_file = Path("data/config/adaptive_depth.json")
        self.adaptive_depth = AdaptiveDepthManager(
            config_file=adaptive_depth_file,
            base_depth=self.max_depth,  # Use configured max_depth as base
            max_depth=self.max_depth + 3  # Allow up to 3 levels deeper for rich sections
        )
        logger.info(f"Adaptive depth manager initialized")

        # Get depth configuration
        depth_config = self.adaptive_depth.get_depth_configuration()
        if depth_config:
            logger.info(f"Loaded adaptive depth configuration for {len(depth_config)} sections")

        # url dedup with a set for backward compatibility
        self.seen_urls = set()

        # counters because apparently we need metrics for everything
        self.total_urls_parsed = 0
        self.unique_urls_found = 0
        self.duplicates_skipped = 0
        self.depth_yields = {i: 0 for i in range(self.max_depth + 1)}
        self.referring_pages = {}  # source_url -> count
        self.seed_count = 0
        self.malformed_seed_skipped = 0
        self.sanitized_seed_count = 0
        self.dynamic_urls_found = 0
        self.api_endpoints_found = 0

        # Feature flags for heuristics
        self.enable_data_attribute_discovery = self.settings.getbool('ENABLE_DATA_ATTRIBUTE_DISCOVERY', True)
        self.enable_form_action_discovery = self.settings.getbool('ENABLE_FORM_ACTION_DISCOVERY', True)
        self.enable_ajax_regex = self.settings.getbool('ENABLE_AJAX_REGEX', True)
        self.enable_json_discovery = self.settings.getbool('ENABLE_JSON_DISCOVERY', True)
        self.enable_pagination_guess = self.settings.getbool('ENABLE_PAGINATION_GUESS', True)
        self.enable_meta_refresh = self.settings.getbool('ENABLE_META_REFRESH_DISCOVERY', True)
        self.enable_html_comment_discovery = self.settings.getbool('ENABLE_HTML_COMMENT_DISCOVERY', True)
        self.enable_css_url_extraction = self.settings.getbool('ENABLE_CSS_URL_EXTRACTION', True)
        self.enable_svg_url_extraction = self.settings.getbool('ENABLE_SVG_URL_EXTRACTION', True)

        logger.info(f"Discovery spider initialized with max_depth={self.max_depth}")
        logger.info(f"Heuristics: JSON={self.enable_json_discovery}, AJAX={self.enable_ajax_regex}, "
                   f"Pagination={self.enable_pagination_guess}, DataAttrs={self.enable_data_attribute_discovery}, "
                   f"Forms={self.enable_form_action_discovery}, MetaRefresh={self.enable_meta_refresh}, "
                   f"HTMLComments={self.enable_html_comment_discovery}, CSS={self.enable_css_url_extraction}, "
                   f"SVG={self.enable_svg_url_extraction}")

    def start_requests(self) -> Iterator[scrapy.Request]:
        """Load seed URLs and start crawling - legacy sync method for backward compatibility"""
        seed_file = Path(self.seed_file)

        if not seed_file.exists():
            logger.error(f"Seed file not found: {seed_file}")
            return

        # attempt to grab some sitemap/robots data because manually updating CSVs is terrible
        yield from self._generate_sitemap_requests()

        logger.info(f"Loading seed URLs from {seed_file}")

        with open(seed_file, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row_num, row in enumerate(reader, 1):
                if row and row[0].strip():
                    raw_url = row[0].strip()
                    cleaned_url, sanitized = self._clean_seed_url(raw_url, row_num)

                    if cleaned_url is None:
                        self.malformed_seed_skipped += 1
                        continue

                    if sanitized:
                        self.sanitized_seed_count += 1
                        logger.debug(
                            f"Sanitized seed URL at line {row_num}: {raw_url} -> {cleaned_url}"
                        )

                    canonical_url = canonicalize_url_simple(cleaned_url)

                    if canonical_url not in self.seen_urls:
                        self.seen_urls.add(canonical_url)
                        self.seed_count += 1
                        yield scrapy.Request(
                            url=canonical_url,
                            callback=self.parse,
                            meta={
                                'source_url': canonical_url,
                                'depth': 0,
                                'first_seen': datetime.now().isoformat(),
                                'playwright': True
                            }
                        )

        # log stats so we know when our CSV gets outdated
        logger.info(f"Loaded {self.seed_count} unique seed URLs")

    async def start(self) -> AsyncGenerator[scrapy.Request, None]:
        """New async start method to replace deprecated start_requests()"""
        # Delegate to the sync method for now to maintain compatibility
        for request in self.start_requests():
            yield request

    def parse(self, response: Response) -> Iterator[DiscoveryItem]:
        """Parse response and extract links"""
        source_url = response.meta['source_url']
        current_depth = response.meta['depth']

        # Set trace ID for this parse request
        trace_id = set_trace_id()
        logger.log_with_context(
            logging.DEBUG,
            "Parse request started",
            url=source_url,
            depth=current_depth,
            trace_id=trace_id
        )

        # Skip non-text responses (images, videos, PDFs, etc.)
        content_type = response.headers.get('Content-Type', b'').decode('utf-8', errors='ignore').lower()
        if not any(text_type in content_type for text_type in ['text/html', 'text/plain', 'application/xhtml', 'application/xml']):
            logger.debug(f"Skipping non-text response: {response.url} (Content-Type: {content_type})")
            return

        # Get adaptive depth limit for this URL's section
        adaptive_max_depth = self.adaptive_depth.get_depth_for_url(source_url)

        # Record this URL's discovery for adaptive learning
        self.adaptive_depth.record_discovery(source_url, current_depth)

        # Extract links from the current page
        le = LinkExtractor(
            allow_domains=self.allowed_domains,
            unique=True,
            deny_extensions=[
                'jpg', 'jpeg', 'png', 'gif', 'svg', 'ico', 'bmp', 'webp',
                'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
                'zip', 'rar', '7z', 'tar', 'gz',
                'mp3', 'mp4', 'avi', 'mov', 'wav', 'flac',
                'css', 'js', 'woff', 'woff2', 'ttf', 'eot',
                'xml', 'rss', 'atom'
            ]
        )

        try:
            links = le.extract_links(response)
        except AttributeError as e:
            if "Response content isn't text" in str(e):
                logger.debug(f"Skipping non-text response during link extraction: {response.url}")
                return
            raise
        self.total_urls_parsed += 1

        if source_url not in self.referring_pages:
            self.referring_pages[source_url] = 0
        self.referring_pages[source_url] += len(links)

        if self.total_urls_parsed % 100 == 0:
            logger.log_with_context(
                logging.INFO,
                "Discovery progress checkpoint",
                pages_parsed=self.total_urls_parsed,
                unique_urls_found=self.unique_urls_found,
                duplicates_skipped=self.duplicates_skipped
            )

        logger.debug(f"Extracted {len(links)} links from {response.url}")

        for link in links:
            # Extract anchor text for importance scoring
            anchor_text = link.text.strip() if hasattr(link, 'text') and link.text else None

            for output in self._process_candidate_url(
                link.url, source_url, current_depth, "html_link", 1.0, anchor_text=anchor_text
            ):
                yield output

        yield from self._discover_dynamic_sources(response, current_depth)

    def _discover_dynamic_sources(self, response: Response, current_depth: int) -> Iterator[DiscoveryItem]:
        """Identify dynamic/AJAX URLs and hidden API endpoints embedded in the page."""

        source_url = response.url
        dynamic_candidates = set()

        # Enhanced throttling for noisy heuristics
        if not hasattr(self, '_dynamic_discovery_stats'):
            self._dynamic_discovery_stats = {
                'total_count': 0,
                'low_quality_count': 0,
                'domain_patterns': {},
                'source_type_stats': {},
                'source_url_quality': {},
                'heuristic_quality': {}
            }

        self._dynamic_discovery_stats['total_count'] += 1

        # Source-specific throttling
        source_quality = self._dynamic_discovery_stats['source_url_quality']
        if source_url in source_quality and source_quality[source_url] > 20:  # Threshold
            logger.debug(f"Throttling noisy source URL: {source_url}")
            return

        # Progressive throttling based on discovery quality
        total_discoveries = self._dynamic_discovery_stats['total_count']
        low_quality_ratio = self._dynamic_discovery_stats['low_quality_count'] / max(1, total_discoveries)

        # If we're finding too many low-quality URLs, throttle aggressively
        if low_quality_ratio > 0.7 and total_discoveries > 50:
            if total_discoveries % 10 != 0:  # Only process every 10th page
                logger.debug(f"Throttling dynamic discovery due to low quality ratio: {low_quality_ratio:.2f}")
                return

        # Domain-specific throttling for known noisy patterns
        domain = urlparse(source_url).netloc
        if domain in self._dynamic_discovery_stats['domain_patterns']:
            domain_count = self._dynamic_discovery_stats['domain_patterns'][domain]
            if domain_count > 200:  # Per-domain limit
                logger.debug(f"Domain {domain} hit discovery limit: {domain_count}")
                return
        else:
            self._dynamic_discovery_stats['domain_patterns'][domain] = 0

        # track candidates by source for proper provenance
        sourced_candidates = {}  # candidate_url -> (source_type, confidence)
        heuristic_quality = self._dynamic_discovery_stats['heuristic_quality']

        # data attributes - medium confidence since they're intentional
        if self.enable_data_attribute_discovery and heuristic_quality.get('data_attribute', 0) < 100:
            for attr in DATA_ATTRIBUTE_CANDIDATES:
                raw_values = response.xpath(f'//*[@{attr}]/@{attr}').getall()
                for raw_value in raw_values:
                    if not raw_value:
                        continue
                    raw_value = raw_value.strip()
                    if not raw_value:
                        continue

                    if raw_value.startswith('{') or raw_value.startswith('['):
                        json_urls = self._extract_urls_from_json_text(raw_value, response)
                        for url in json_urls:
                            sourced_candidates[url] = ("ajax_endpoint", 0.7)
                        continue

                    normalized = self._normalize_candidate(raw_value, response)
                    if normalized:
                        sourced_candidates[normalized] = ("ajax_endpoint", 0.8)

        # HTML comments - very low confidence
        if self.enable_html_comment_discovery and heuristic_quality.get('html_comment', 0) < 100:
            comment_nodes = response.xpath('//comment()').getall()
            for comment in comment_nodes:
                for match in SCRIPT_URL_PATTERN.finditer(comment):
                    raw_candidate = match.group('url')
                    normalized = self._normalize_candidate(raw_candidate, response)
                    if normalized:
                        sourced_candidates[normalized] = ("html_comment", 0.2)

        # Inline CSS background-image URLs - low confidence
        if self.enable_css_url_extraction and heuristic_quality.get('css_inline_style', 0) < 100:
            style_attrs = response.xpath('//@style').getall()
            for style in style_attrs:
                # A simple regex to find url(...) declarations
                for match in re.finditer(r'url\(([^)]+)\)', style):
                    raw_url = match.group(1)
                    # Remove quotes
                    raw_url = raw_url.strip('"')
                    normalized = self._normalize_candidate(raw_url, response)
                    if normalized:
                        sourced_candidates[normalized] = ("css_inline_style", 0.3)

        # SVG embedded URLs - low confidence
        if self.enable_svg_url_extraction and heuristic_quality.get('svg_embedded', 0) < 100:
            # Find all hrefs within inline SVGs (handle namespaces properly)
            try:
                svg_hrefs = response.xpath('//svg//@href').getall()
                # Try xlink:href with proper namespace if available
                try:
                    svg_xlink_hrefs = response.xpath('//svg//@*[local-name()="href"]').getall()
                    svg_hrefs.extend(svg_xlink_hrefs)
                except Exception:
                    pass
            except Exception:
                svg_hrefs = []

            for raw_url in svg_hrefs:
                normalized = self._normalize_candidate(raw_url, response)
                if normalized:
                    sourced_candidates[normalized] = ("svg_embedded", 0.4)

            # Find style attributes and tags within SVGs
            svg_styles = response.xpath('//svg//@style | //svg//style/text()').getall()
            for style in svg_styles:
                for match in re.finditer(r'url\(([^)]+)\)', style):
                    raw_url = match.group(1).strip('"')
                    normalized = self._normalize_candidate(raw_url, response)
                    if normalized:
                        sourced_candidates[normalized] = ("svg_embedded_css", 0.3)

        # form actions - high confidence since they're explicit endpoints
        if self.enable_form_action_discovery and heuristic_quality.get('form_action', 0) < 100:
            for raw_action in response.xpath('//form[@action]/@action').getall():
                normalized = self._normalize_candidate(raw_action, response)
                if normalized:
                    sourced_candidates[normalized] = ("ajax_endpoint", 0.9)

        # javascript patterns - lower confidence since they might be templates
        if self.enable_ajax_regex and heuristic_quality.get('ajax_regex', 0) < 100:
            script_texts = response.xpath('//script[not(@src)]/text()').getall()
            for script in script_texts:
                if not script:
                    continue
                lowered = script.lower()
                if not self._contains_dynamic_hint(lowered):
                    continue

                for match in SCRIPT_URL_PATTERN.finditer(script):
                    raw_candidate = match.group('url')
                    normalized = self._normalize_candidate(raw_candidate, response)
                    if normalized:
                        sourced_candidates[normalized] = ("ajax_endpoint", 0.6)

        # json script blocks - medium confidence
        if self.enable_json_discovery and heuristic_quality.get('json_discovery', 0) < 100:
            json_scripts = response.xpath('//script[contains(@type, "json")]/text()').getall()
            for raw_json in json_scripts:
                json_urls = self._extract_urls_from_json_text(raw_json, response)
                for url in json_urls:
                    sourced_candidates[url] = ("json_blob", 0.7)

        # meta refresh redirects - high confidence since they're explicit
        if self.enable_meta_refresh and heuristic_quality.get('meta_refresh', 0) < 100:
            # Extract from <meta http-equiv="refresh" content="0; url=..." >
            meta_refresh = response.xpath('//meta[contains(@http-equiv, "refresh")]/@content').getall()
            for content in meta_refresh:
                # Parse content like "5; url=http://example.com"
                if 'url=' in content.lower():
                    parts = content.lower().split('url=')
                    if len(parts) > 1:
                        raw_url = parts[1].strip().strip('"').strip("'")
                        normalized = self._normalize_candidate(raw_url, response)
                        if normalized:
                            sourced_candidates[normalized] = ("meta_refresh", 0.9)

        # pagination generation - lower confidence since it's speculative
        if self.enable_pagination_guess and heuristic_quality.get('pagination_guess', 0) < 100:
            pagination_candidates = set()
            for candidate in sourced_candidates.keys():
                if self._looks_like_api_endpoint(candidate):
                    pagination_urls = self._generate_pagination_urls(candidate)
                    for pag_url in pagination_urls:
                        pagination_candidates.add(pag_url)

            for pag_url in pagination_candidates:
                sourced_candidates[pag_url] = ("pagination", 0.4)

        # process all candidates with their provenance and quality tracking
        quality_urls_found = 0
        for candidate, (source_type, confidence) in sourced_candidates.items():
            results = self._process_candidate_url(
                candidate, source_url, current_depth, source_type, confidence
            )
            if not results:
                continue

            self.dynamic_urls_found += 1
            if self._looks_like_api_endpoint(candidate):
                self.api_endpoints_found += 1

            # Track quality for throttling decisions
            if confidence >= 0.6:
                quality_urls_found += 1
            else:
                self._dynamic_discovery_stats['low_quality_count'] += 1
                if source_url not in source_quality:
                    source_quality[source_url] = 0
                source_quality[source_url] += 1

                # Update heuristic quality stats
                if source_type not in heuristic_quality:
                    heuristic_quality[source_type] = 0
                heuristic_quality[source_type] += 1

            # Update source type statistics
            if source_type not in self._dynamic_discovery_stats['source_type_stats']:
                self._dynamic_discovery_stats['source_type_stats'][source_type] = 0
            self._dynamic_discovery_stats['source_type_stats'][source_type] += 1

            for result in results:
                yield result

        # Update domain pattern tracking
        domain = urlparse(source_url).netloc
        self._dynamic_discovery_stats['domain_patterns'][domain] += len(sourced_candidates)

    def _process_candidate_url(
        self,
        candidate_url: str,
        source_url: str,
        current_depth: int,
        discovery_source: str = "html_link",
        confidence: float = 1.0,
        anchor_text: str = None,
    ) -> list:
        """process URLs and pretend we're being efficient"""

        # Check if source is throttled based on feedback
        if discovery_source in self.throttled_sources:
            logger.debug(f"Skipping URL from throttled source '{discovery_source}': {candidate_url}")
            return []

        canonical_url = canonicalize_url_simple(candidate_url)

        # Check if URL pattern is known to be low quality
        url_pattern = self.feedback_store.extract_url_pattern(canonical_url)
        if url_pattern in self.low_quality_patterns:
            logger.debug(f"Skipping low-quality URL pattern '{url_pattern}': {canonical_url}")
            return []

        parsed = urlparse(canonical_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            logger.debug(f"Ignoring invalid candidate URL {candidate_url}")
            return []

        # Adjust confidence based on historical performance
        adjusted_confidence = self.feedback_store.get_adjusted_confidence(
            canonical_url, discovery_source, confidence
        )

        # Record discovery in feedback store
        self.feedback_store.record_discovery(canonical_url, discovery_source, adjusted_confidence)

        # Calculate importance score (blend of signals)
        importance_score = self._calculate_importance_score(
            canonical_url, source_url, discovery_source, adjusted_confidence, anchor_text
        )

        # Check if same domain
        source_domain = urlparse(source_url).netloc
        target_domain = urlparse(canonical_url).netloc
        is_same_domain = source_domain == target_domain

        url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()

        # Use persistent cache if available, otherwise fallback to in-memory
        if self.url_cache:
            # Atomic check-and-insert with SQLite
            is_new = self.url_cache.add_url_if_new(canonical_url, url_hash)
            if not is_new:
                self.duplicates_skipped += 1
                return []
            # Update in-memory sets for fast lookups
            self.url_hashes.add(url_hash)
            self.seen_urls.add(canonical_url)
        else:
            # In-memory deduplication
            if url_hash in self.url_hashes or canonical_url in self.seen_urls:
                self.duplicates_skipped += 1
                return []
            self.seen_urls.add(canonical_url)
            self.url_hashes.add(url_hash)

        self.unique_urls_found += 1

        next_depth = current_depth + 1
        if next_depth <= self.max_depth:
            self.depth_yields[next_depth] += 1

        discovery_time = datetime.now().isoformat()

        # Generate trace ID for this discovered URL (for logging only, not stored in item)
        from src.common.logging import get_session_id, get_trace_id
        item_trace_id = set_trace_id()

        results = [
            DiscoveryItem(
                source_url=source_url,
                discovered_url=canonical_url,
                first_seen=discovery_time,
                url_hash=url_hash,
                discovery_depth=next_depth,
                discovery_source=discovery_source,
                confidence=adjusted_confidence,  # Use feedback-adjusted confidence
                importance_score=importance_score,
                anchor_text=anchor_text,
                is_same_domain=is_same_domain
            )
        ]

        # Use adaptive depth for this URL's section
        adaptive_max_depth = self.adaptive_depth.get_depth_for_url(canonical_url)

        if current_depth < adaptive_max_depth:
            results.append(
                scrapy.Request(
                    url=canonical_url,
                    callback=self.parse,
                    meta={
                        'source_url': canonical_url,
                        'depth': next_depth,
                        'first_seen': discovery_time,
                        'url_hash': url_hash
                    }
                )
            )

        return results

    def _calculate_importance_score(
        self,
        url: str,
        source_url: str,
        discovery_source: str,
        confidence: float,
        anchor_text: Optional[str]
    ) -> float:
        """
        Calculate importance score blending multiple signals:
        - Discovery confidence (30%)
        - Anchor text quality (20%)
        - Same-domain boost (15%)
        - URL path depth penalty (15%)
        - Discovery source priority (20%)
        """
        score = 0.0

        # Base score from discovery confidence (0.0-0.3)
        score += confidence * 0.3

        # Anchor text quality score (0.0-0.2)
        if anchor_text:
            anchor_lower = anchor_text.lower()
            # High-value keywords
            high_value_terms = [
                'research', 'publication', 'faculty', 'department', 'course',
                'program', 'academic', 'study', 'lab', 'center', 'institute'
            ]
            medium_value_terms = [
                'news', 'event', 'about', 'contact', 'resource', 'library',
                'student', 'staff', 'admission', 'undergraduate', 'graduate'
            ]

            if any(term in anchor_lower for term in high_value_terms):
                score += 0.2
            elif any(term in anchor_lower for term in medium_value_terms):
                score += 0.1
            elif len(anchor_text) > 5:  # Descriptive anchor text
                score += 0.05

        # Same-domain boost (0.0-0.15)
        source_domain = urlparse(source_url).netloc
        target_domain = urlparse(url).netloc
        if source_domain == target_domain:
            score += 0.15

        # URL path depth penalty (0.0-0.15)
        # Shorter paths are typically more important
        path = urlparse(url).path
        path_depth = len([p for p in path.split('/') if p])
        if path_depth == 0 or path_depth == 1:
            score += 0.15
        elif path_depth == 2:
            score += 0.10
        elif path_depth == 3:
            score += 0.05

        # Discovery source priority (0.0-0.2)
        source_priority = {
            'html_link': 0.20,
            'sitemap': 0.18,
            'ajax_endpoint': 0.15,
            'meta_refresh': 0.15,
            'json_blob': 0.10,
            'data_attribute': 0.08,
            'pagination': 0.05,
            'html_comment': 0.02
        }
        score += source_priority.get(discovery_source, 0.05)

        # Normalize to 0.0-1.0 range
        return min(1.0, max(0.0, score))

    def _normalize_candidate(self, raw_url: str, response: Response) -> Optional[str]:
        """Normalise dynamic candidate URLs and enforce domain boundaries."""

        if not raw_url:
            return None

        candidate = raw_url.strip()

        if not candidate or candidate.startswith(('#', 'javascript:', 'mailto:')):
            return None

        absolute = response.urljoin(candidate)
        parsed = urlparse(absolute)

        if parsed.scheme not in ('http', 'https'):
            return None

        hostname = (parsed.hostname or '').lower()
        if hostname:
            # Check if hostname matches any of the allowed domains
            allowed = any(hostname.endswith(domain) for domain in self.allowed_domains)
            if not allowed:
                return None

        return absolute

    def _extract_urls_from_json_text(self, raw_value: str, response: Response) -> set:
        """Parse JSON strings and gather plausible URL values recursively."""

        stripped = raw_value.strip().rstrip(';')

        if not stripped:
            return set()

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as e:
            logger.debug(f"Failed to parse JSON from {response.url}: {e}")
            return set()

        return self._extract_urls_from_json(payload, response)

    def _extract_urls_from_json(self, payload, response: Response) -> set:
        urls = set()

        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(value, str):
                    if not value:
                        continue
                    if key and key.lower() not in JSON_URL_KEY_HINTS and 'url' not in key.lower():
                        # Only attempt heuristics when keys imply a link.
                        # Check against allowed domains
                        has_allowed_domain = any(domain in value for domain in self.allowed_domains)
                        if not has_allowed_domain and '/api/' not in value and '.json' not in value:
                            continue
                    normalized = self._normalize_candidate(value, response)
                    if normalized:
                        urls.add(normalized)
                else:
                    urls.update(self._extract_urls_from_json(value, response))

        elif isinstance(payload, list):
            for item in payload:
                urls.update(self._extract_urls_from_json(item, response))

        elif isinstance(payload, str):
            normalized = self._normalize_candidate(payload, response)
            if normalized:
                urls.add(normalized)

        return urls

    def _looks_like_api_endpoint(self, url: str) -> bool:
        parsed = urlparse(url)
        path = (parsed.path or '').lower()
        query = (parsed.query or '').lower()

        if any(token in path for token in ('/api/', '/rest/', '/services/', '/ajax/', '/feed')):
            return True

        if path.endswith(('.json', '.xml', '.csv')):
            return True

        return any(token in query for token in ('api', 'format=json', 'view=ajax'))

    def _contains_dynamic_hint(self, script_text: str) -> bool:
        return any(hint in script_text for hint in DYNAMIC_SCRIPT_HINTS)

    async def _discover_with_headless_browser(self, url: str, current_depth: int) -> Iterator[DiscoveryItem]:
        """
        Use enhanced headless browser to discover URLs from JavaScript-rendered content.
        Supports network interception, auto-click, SPA navigation, and infinite scroll.
        """
        try:
            from src.common.enhanced_browser import EnhancedBrowserDiscovery

            # Get headless browser config from settings
            browser_config = self.settings.get('HEADLESS_BROWSER_CONFIG', {})
            if not browser_config.get('enabled', False):
                logger.debug(f"Headless browser disabled, skipping: {url}")
                return

            logger.info(f"Using enhanced browser for JavaScript-heavy page: {url}")

            # Initialize and start browser with enhanced features
            browser = EnhancedBrowserDiscovery(browser_config)
            await browser.start()

            try:
                # Extract base domain from allowed_domains
                base_domain = self.allowed_domains[0] if self.allowed_domains else 'uconn.edu'

                # Discover URLs using all available techniques
                result = await browser.discover_urls(url, base_domain)

                # Extract discovered URLs from all sources
                discovered_urls = set(result.get('discovered_urls', []))
                network_urls = set(result.get('network_urls', []))
                all_urls = discovered_urls | network_urls

                logger.info(f"Enhanced browser discovered {len(all_urls)} total URLs from {url}")
                logger.info(f"  - Static HTML: {result['discovery_methods']['static_html']}")
                logger.info(f"  - Auto-click: {result['discovery_methods']['auto_click']}")
                logger.info(f"  - Infinite scroll: {result['discovery_methods']['infinite_scroll']}")
                logger.info(f"  - Network intercept: {result['discovery_methods']['network_intercept']}")

                # Process discovered URLs with appropriate confidence scores
                for discovered_url in all_urls:
                    normalized = self._normalize_candidate(discovered_url, None)
                    if normalized:
                        # Higher confidence for URLs discovered via multiple methods
                        confidence = 0.9 if discovered_url in network_urls else 0.8

                        results = self._process_candidate_url(
                            normalized, url, current_depth, "enhanced_browser", confidence
                        )
                        if results:
                            yield results

            finally:
                await browser.stop()

        except ImportError:
            logger.warning("Enhanced browser module not available. Install playwright: pip install playwright && playwright install")
        except Exception as e:
            logger.error(f"Enhanced browser discovery failed for {url}: {e}")

    def closed(self, reason):
        """Called when spider closes - report comprehensive crawl summary"""
        logger.info("=" * 80)
        logger.info("DISCOVERY SPIDER CRAWL SUMMARY")
        logger.info("=" * 80)

        # Overall statistics
        logger.info(f"Crawl completed: {reason}")
        logger.info(f"Total pages parsed: {self.total_urls_parsed:,}")
        logger.info(f"Unique URLs found: {self.unique_urls_found:,}")
        logger.info(f"Duplicates skipped: {self.duplicates_skipped:,}")
        logger.info(f"Seed URLs loaded: {self.seed_count:,}")
        logger.info(f"Sanitized seed URLs recovered: {self.sanitized_seed_count:,}")
        logger.info(f"Malformed seeds skipped: {self.malformed_seed_skipped:,}")
        logger.info(f"Dynamic/AJAX URLs discovered: {self.dynamic_urls_found:,}")
        logger.info(f"Hidden API endpoints discovered: {self.api_endpoints_found:,}")

        # Depth histogram for coverage analysis
        logger.info("-" * 40)
        logger.info("DEPTH DISTRIBUTION:")
        total_discovered = sum(self.depth_yields.values())
        for depth in sorted(self.depth_yields.keys()):
            count = self.depth_yields[depth]
            percentage = (count / total_discovered * 100) if total_discovered > 0 else 0
            logger.info(f"  Depth {depth}: {count:,} URLs ({percentage:.1f}%)")

        # Top referring pages (helps prune noisy seeds)
        logger.info("-" * 40)
        logger.info("TOP REFERRING PAGES (for pruning noisy seeds):")
        sorted_referrers = sorted(self.referring_pages.items(), key=lambda x: x[1], reverse=True)
        for i, (source_url, link_count) in enumerate(sorted_referrers[:10], 1):
            logger.info(f"  {i}. {link_count:,} links from {source_url}")

        # Dynamic discovery throttling statistics
        if hasattr(self, '_dynamic_discovery_stats'):
            stats = self._dynamic_discovery_stats
            logger.info("-" * 40)
            logger.info("DYNAMIC DISCOVERY THROTTLING STATS:")
            logger.info(f"Total dynamic discoveries attempted: {stats['total_count']:,}")
            logger.info(f"Low quality URLs found: {stats['low_quality_count']:,}")

            if stats['total_count'] > 0:
                quality_ratio = (stats['total_count'] - stats['low_quality_count']) / stats['total_count']
                logger.info(f"Quality ratio: {quality_ratio:.2f} (higher is better)")

            # Source type breakdown
            if stats['source_type_stats']:
                logger.info("Discovery sources:")
                for source_type, count in sorted(stats['source_type_stats'].items(), key=lambda x: x[1], reverse=True):
                    logger.info(f"  {source_type}: {count:,}")

            # Top domains by discovery count
            if stats['domain_patterns']:
                logger.info("Top discovery domains:")
                for domain, count in sorted(stats['domain_patterns'].items(), key=lambda x: x[1], reverse=True)[:5]:
                    logger.info(f"  {domain}: {count:,} candidates")

        # Efficiency metrics
        duplicate_rate = (self.duplicates_skipped / max(1, self.unique_urls_found + self.duplicates_skipped)) * 100
        logger.info("-" * 40)
        logger.info("EFFICIENCY METRICS:")
        logger.info(f"Duplicate rate: {duplicate_rate:.1f}% (lower is better)")
        logger.info(f"Discovery rate: {self.unique_urls_found / max(1, self.total_urls_parsed):.1f} URLs/page")

        # Save adaptive depth configuration for next crawl
        logger.info("-" * 40)
        self.adaptive_depth.save_config()
        self.adaptive_depth.print_report()

        logger.info("=" * 80)

    def _clean_seed_url(self, raw_url: str, line_number: int) -> Tuple[Optional[str], bool]:
        """Attempt to clean malformed seed URLs while preserving usable entries."""

        url = raw_url.strip()
        if not url:
            return None, False

        sanitized = False
        lower_url = url.lower()

        # Remove stray wrapping quotes left over from CSV escapes.
        trimmed = url.strip("'")
        if trimmed != url:
            url = trimmed
            lower_url = url.lower()
            sanitized = True

        # Strip trailing backslashes accidentally captured in some exports.
        if url.endswith('\\'):
            url = url.rstrip('\\')
            lower_url = url.lower()
            sanitized = True

        # Recover when a scheme is duplicated (e.g. "https:https://...").
        for scheme in ("https://", "http://"):
            idx = lower_url.find(scheme)
            if idx > 0:
                url = url[idx:]
                sanitized = True
                lower_url = url.lower()
                break

        # Extract final URL from Windows cache paths captured in the CSV.
        if "\\" in url:
            parts = [segment for segment in url.split("\\") if segment]
            recovered = None
            for segment in reversed(parts):
                candidate = segment.strip().split()[0]
                if candidate.lower().endswith(".uconn.edu"):
                    recovered = f"https://{candidate}"
                    break

            if recovered is None:
                logger.warning(
                    f"Skipping malformed seed URL at line {line_number}: {raw_url}"
                )
                return None, sanitized

            url = recovered
            lower_url = url.lower()
            sanitized = True

        # Default to https when scheme missing.
        parsed = urlparse(url)
        if not parsed.scheme:
            url = f"https://{url.lstrip('/') }"
            parsed = urlparse(url)
            sanitized = True

        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            logger.warning(
                f"Skipping malformed seed URL at line {line_number}: {raw_url}"
            )
            return None, sanitized

        return url, sanitized

    def _generate_sitemap_requests(self) -> Iterator[scrapy.Request]:
        """Generate requests for common sitemap/robots locations because automation beats manual updates"""
        # Generate base domains from allowed domains
        base_domains = []
        for domain in self.allowed_domains:
            base_domains.append(domain)
            if not domain.startswith('www.'):
                base_domains.append(f'www.{domain}')

        for domain in base_domains:
            # check robots.txt first
            robots_url = f"https://{domain}/robots.txt"
            yield scrapy.Request(
                url=robots_url,
                callback=self._parse_robots,
                meta={
                    'domain': domain,
                    'depth': 0,
                    'source_url': robots_url,
                    'first_seen': datetime.now().isoformat()
                },
                dont_filter=True
            )

            # common sitemap locations
            sitemap_paths = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"]
            for path in sitemap_paths:
                sitemap_url = f"https://{domain}{path}"
                yield scrapy.Request(
                    url=sitemap_url,
                    callback=self._parse_sitemap,
                    meta={
                        'domain': domain,
                        'depth': 0,
                        'source_url': sitemap_url,
                        'first_seen': datetime.now().isoformat()
                    },
                    dont_filter=True
                )

    def _parse_robots(self, response: Response) -> Iterator[scrapy.Request]:
        """Parse robots.txt for sitemap references"""
        if response.status != 200:
            return

        for line in response.text.splitlines():
            line = line.strip()
            if line.lower().startswith('sitemap:'):
                sitemap_url = line.split(':', 1)[1].strip()
                # Check if sitemap_url contains any allowed domain
                has_allowed_domain = any(domain in sitemap_url for domain in self.allowed_domains)
                if sitemap_url and has_allowed_domain:
                    yield scrapy.Request(
                        url=sitemap_url,
                        callback=self._parse_sitemap,
                        meta=response.meta,
                        dont_filter=True
                    )

    def _parse_sitemap(self, response: Response) -> Iterator[DiscoveryItem]:
        """Parse sitemap XML for URLs"""
        if response.status != 200:
            return

        # basic XML parsing without heavy dependencies
        import re
        source_url = response.meta['source_url']

        # extract URLs from sitemap XML
        url_pattern = re.compile(r'<loc>(https?://[^<]+)</loc>', re.IGNORECASE)
        sitemap_pattern = re.compile(r'<sitemap>.*?<loc>(https?://[^<]+)</loc>.*?</sitemap>', re.IGNORECASE | re.DOTALL)

        # check for nested sitemaps first
        for sitemap_match in sitemap_pattern.finditer(response.text):
            nested_sitemap_url = sitemap_match.group(1)
            has_allowed_domain = any(domain in nested_sitemap_url for domain in self.allowed_domains)
            if has_allowed_domain:
                yield scrapy.Request(
                    url=nested_sitemap_url,
                    callback=self._parse_sitemap,
                    meta=response.meta,
                    dont_filter=True
                )

        # extract actual URLs
        for url_match in url_pattern.finditer(response.text):
            url = url_match.group(1)
            canonical_url = canonicalize_url_simple(url)
            if canonical_url not in self.seen_urls:
                url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
                if url_hash in self.url_hashes:
                    continue

                self.seen_urls.add(canonical_url)
                self.url_hashes.add(url_hash)
                self.unique_urls_found += 1

                yield DiscoveryItem(
                    source_url=source_url,
                    discovered_url=canonical_url,
                    first_seen=datetime.now().isoformat(),
                    url_hash=url_hash,
                    discovery_depth=0,
                    discovery_source="sitemap",
                    confidence=0.95
                )

    def _generate_pagination_urls(self, base_url: str, limit: int = 10) -> set:
        """Generate common pagination patterns for API endpoints with TTL-aware caching"""
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        pagination_urls = set()

        # Get last valid page from cache (respects TTL)
        last_valid_page = self.pagination_cache.get_last_valid_page(base_url)

        # Clean up expired entries periodically
        if hasattr(self, '_pagination_cleanup_counter'):
            self._pagination_cleanup_counter += 1
            if self._pagination_cleanup_counter % 100 == 0:
                self.pagination_cache.cleanup_expired()
        else:
            self._pagination_cleanup_counter = 1

        parsed = urlparse(base_url)
        query_params = parse_qs(parsed.query)

        # Common pagination parameters to try
        # Start from last valid + 1, or 1 if no history
        start_page = max(1, last_valid_page)

        pagination_patterns = [
            {'page': list(range(start_page, start_page + limit))},
            {'p': list(range(start_page, start_page + limit))},
            {'offset': [i * 10 for i in range(start_page, start_page + limit)]},
            {'start': [i * 10 for i in range(start_page, start_page + limit)]},
        ]

        generated_count = 0
        for pattern in pagination_patterns:
            for param, values in pattern.items():
                for page_num_or_offset in values:
                    # Use should_attempt_page to respect success rates and TTL
                    page_num = page_num_or_offset if param in ['page', 'p'] else page_num_or_offset // 10

                    if not self.pagination_cache.should_attempt_page(base_url, page_num, max_pages=limit):
                        logger.debug(f"Skipping pagination page {page_num} for {base_url} (TTL/success rate check)")
                        continue

                    new_params = query_params.copy()
                    new_params[param] = [str(page_num_or_offset)]
                    new_query = urlencode(new_params, doseq=True)
                    new_url = urlunparse(
                        (
                            parsed.scheme, parsed.netloc, parsed.path,
                            parsed.params, new_query, parsed.fragment
                        )
                    )
                    pagination_urls.add(new_url)
                    generated_count += 1

        if generated_count > 0:
            logger.debug(f"Generated {generated_count} pagination URLs for {base_url} (starting from page {start_page})")

        return pagination_urls
