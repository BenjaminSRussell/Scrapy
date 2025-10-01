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

from src.common.schemas import DiscoveryItem
from src.common.urls import canonicalize_url_simple
from src.common.storage import URLCache, PaginationCache


logger = logging.getLogger(__name__)  # Because we need to know what went wrong


DYNAMIC_SCRIPT_HINTS = (
    'fetch(', 'xmlhttprequest', 'axios', '$.get', '$.post', '.ajax', 'loadmore',
    'nexturl', 'next_url', 'load_more', 'apiurl', 'api_url', 'dispatch('
)

SCRIPT_URL_PATTERN = re.compile(
    r'["\'](?P<url>(?:https?:)?//[\w\.-]+(?:/[\w\./\?-]*)?|/[\w\./\?-]+)["\']',
    re.IGNORECASE
)

JSON_URL_KEY_HINTS = {'url', 'href', 'link', 'endpoint', 'action', 'download'}

DATA_ATTRIBUTE_CANDIDATES = (
    'data-url', 'data-src', 'data-endpoint', 'data-load', 'data-load-url',
    'data-href', 'data-link', 'data-api', 'data-request-url', 'data-action',
    'data-next', 'data-feed'
)


class DiscoverySpider(scrapy.Spider):
    """Stage 1 Discovery Spider - finds and catalogs new URLs"""

    name = "discovery"

    def __init__(self, max_depth: int = 3, allowed_domains: list = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_depth = int(max_depth)

        # Load allowed domains from configuration or use default
        if allowed_domains:
            if isinstance(allowed_domains, str):
                # Handle comma-separated string
                self.allowed_domains = [d.strip() for d in allowed_domains.split(',')]
            else:
                self.allowed_domains = allowed_domains
        else:
            self.allowed_domains = ["uconn.edu"]

        logger.info(f"Allowed domains: {self.allowed_domains}")

        # Initialize persistent deduplication if enabled
        use_persistent_dedup = self.settings.getbool('USE_PERSISTENT_DEDUP', True)
        dedup_cache_path = self.settings.get('DEDUP_CACHE_PATH', 'data/cache/url_cache.db')

        if use_persistent_dedup:
            self.url_cache = URLCache(Path(dedup_cache_path))
            logger.info(f"Using persistent deduplication with SQLite: {dedup_cache_path}")
            # Load existing hashes for in-memory fallback
            self.url_hashes = self.url_cache.get_all_hashes()
            logger.info(f"Loaded {len(self.url_hashes)} existing URL hashes from cache")
        else:
            self.url_cache = None
            self.url_hashes = set()
            logger.info("Using in-memory deduplication")

        # Initialize pagination cache
        pagination_cache_path = self.settings.get('PAGINATION_CACHE_PATH', 'data/cache/pagination_cache.db')
        self.pagination_cache = PaginationCache(Path(pagination_cache_path))

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

        logger.info(f"Discovery spider initialized with max_depth={self.max_depth}")

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
                                'first_seen': datetime.now().isoformat()
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

        # Extract links from the current page
        # TODO: The link extractor is not very flexible. It should be made more configurable, such as allowing the user to specify custom deny rules.
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

        links = le.extract_links(response)
        self.total_urls_parsed += 1

        if source_url not in self.referring_pages:
            self.referring_pages[source_url] = 0
        self.referring_pages[source_url] += len(links)

        if self.total_urls_parsed % 100 == 0:
            logger.info(f"Progress: {self.total_urls_parsed} pages, {self.unique_urls_found} URLs, {self.duplicates_skipped} duplicates")

        logger.debug(f"Extracted {len(links)} links from {response.url}")

        for link in links:
            for output in self._process_candidate_url(
                link.url, source_url, current_depth, "html_link", 1.0
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
    ) -> list:
        """process URLs and pretend we're being efficient"""

        canonical_url = canonicalize_url_simple(candidate_url)

        parsed = urlparse(canonical_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            logger.debug(f"Ignoring invalid candidate URL {candidate_url}")
            return []

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

        results = [
            DiscoveryItem(
                source_url=source_url,
                discovered_url=canonical_url,
                first_seen=discovery_time,
                url_hash=url_hash,
                discovery_depth=next_depth,
                discovery_source=discovery_source,
                confidence=confidence
            )
        ]

        if current_depth < self.max_depth:
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

        payload = json.loads(stripped)

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
        """Generate common pagination patterns for API endpoints with caching"""
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        pagination_urls = set()
        last_valid_page = self.pagination_cache.get_last_valid_page(base_url)
        start_page = last_valid_page + 1

        parsed = urlparse(base_url)
        query_params = parse_qs(parsed.query)

        # common pagination parameters to try
        pagination_patterns = [
            {'page': [str(i) for i in range(start_page, start_page + limit)]},
            {'p': [str(i) for i in range(start_page, start_page + limit)]},
            {'offset': [str(i * 10) for i in range(start_page, start_page + limit)]},
            {'start': [str(i * 10) for i in range(start_page, start_page + limit)]},
        ]

        for pattern in pagination_patterns:
            for param, values in pattern.items():
                for value in values:
                    new_params = query_params.copy()
                    new_params[param] = [value]
                    new_query = urlencode(new_params, doseq=True)
                    new_url = urlunparse((
                        parsed.scheme, parsed.netloc, parsed.path,
                        parsed.params, new_query, parsed.fragment
                    ))
                    pagination_urls.add(new_url)

        return pagination_urls
