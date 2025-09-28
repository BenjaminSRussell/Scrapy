import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Tuple
from urllib.parse import urlparse

import scrapy
from scrapy.linkextractors import LinkExtractor
from scrapy.http import Response

from common.urls import canonicalize_url_simple
from common.schemas import DiscoveryItem


DYNAMIC_SCRIPT_HINTS = (
    'fetch(', 'xmlhttprequest', 'axios', '$.get', '$.post', '.ajax', 'loadmore',
    'nexturl', 'next_url', 'load_more', 'apiurl', 'api_url', 'dispatch('
)

SCRIPT_URL_PATTERN = re.compile(r'["\'](?P<url>(?:https?:)?//[^"\']+|/[^\s"\']+)["\']', re.IGNORECASE)

JSON_URL_KEY_HINTS = {'url', 'href', 'link', 'endpoint', 'action', 'download'}

DATA_ATTRIBUTE_CANDIDATES = (
    'data-url', 'data-src', 'data-endpoint', 'data-load', 'data-load-url',
    'data-href', 'data-link', 'data-api', 'data-request-url', 'data-action',
    'data-next', 'data-feed'
)


class DiscoverySpider(scrapy.Spider):
    """Stage 1 Discovery Spider - finds and catalogs new URLs"""

    name = "discovery"
    allowed_domains = ["uconn.edu"]

    def __init__(self, max_depth: int = 3, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_depth = int(max_depth)

        # url dedup with a set because fancy databases are overkill
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

        self.logger.info(f"Discovery spider initialized with max_depth={self.max_depth}")

    def start_requests(self) -> Iterator[scrapy.Request]:
        """Load seed URLs and start crawling"""
        seed_file = Path("data/raw/uconn_urls.csv")

        # attempt to grab some sitemap/robots data because manually updating CSVs is terrible
        yield from self._generate_sitemap_requests()

        if not seed_file.exists():
            self.logger.error(f"Seed file not found: {seed_file}")
            return

        self.logger.info(f"Loading seed URLs from {seed_file}")

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
                        self.logger.debug(
                            f"Sanitized seed URL at line {row_num}: {raw_url} -> {cleaned_url}"
                        )

                    try:
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
                    except Exception as e:
                        self.logger.error(
                            f"Error processing seed URL at line {row_num}: {cleaned_url} - {e}"
                        )

        # log stats so we know when our CSV gets outdated
        self.logger.info(f"Loaded {self.seed_count} unique seed URLs")

    def parse(self, response: Response) -> Iterator[DiscoveryItem]:
        """Parse response and extract links"""
        source_url = response.meta['source_url']
        current_depth = response.meta['depth']

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
            self.total_urls_parsed += 1  # count stuff

            # Track referring pages for summary
            if source_url not in self.referring_pages:
                self.referring_pages[source_url] = 0
            self.referring_pages[source_url] += len(links)

            # spam logs every 100 pages because micromanagement
            if self.total_urls_parsed % 100 == 0:
                self.logger.info(f"Crawl Progress: {self.total_urls_parsed} pages parsed, "
                               f"{self.unique_urls_found} unique URLs, "
                               f"{self.duplicates_skipped} duplicates skipped")

            self.logger.debug(f"Extracted {len(links)} links from {response.url}")

            for link in links:
                try:
                    for output in self._process_candidate_url(
                        link.url, source_url, current_depth, "html_link", 1.0
                    ):
                        yield output
                except Exception as e:
                    self.logger.error(f"Error processing link {link.url}: {e}")

        except Exception as e:
            self.logger.error(f"Error extracting links from {response.url}: {e}")

        try:
            yield from self._discover_dynamic_sources(response, current_depth)
        except Exception as e:
            self.logger.error(f"Error discovering dynamic URLs from {response.url}: {e}")

    def _discover_dynamic_sources(self, response: Response, current_depth: int) -> Iterator[DiscoveryItem]:
        """Identify dynamic/AJAX URLs and hidden API endpoints embedded in the page."""

        source_url = response.url
        dynamic_candidates = set()

        # basic rate limiting to prevent dynamic discovery from going crazy
        if hasattr(self, '_dynamic_discovery_count'):
            self._dynamic_discovery_count += 1
        else:
            self._dynamic_discovery_count = 1

        # arbitrary limit to keep things sane
        if self._dynamic_discovery_count > 1000:
            self.logger.debug(f"Skipping dynamic discovery for {source_url} - hit rate limit")
            return

        # track candidates by source for proper provenance
        sourced_candidates = {}  # candidate_url -> (source_type, confidence)

        # data attributes - medium confidence since they're intentional
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
        for raw_action in response.xpath('//form[@action]/@action').getall():
            normalized = self._normalize_candidate(raw_action, response)
            if normalized:
                sourced_candidates[normalized] = ("ajax_endpoint", 0.9)

        # javascript patterns - lower confidence since they might be templates
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
        json_scripts = response.xpath('//script[contains(@type, "json")]/text()').getall()
        for raw_json in json_scripts:
            json_urls = self._extract_urls_from_json_text(raw_json, response)
            for url in json_urls:
                sourced_candidates[url] = ("json_blob", 0.7)

        # pagination generation - lower confidence since it's speculative
        pagination_candidates = set()
        for candidate in sourced_candidates.keys():
            if self._looks_like_api_endpoint(candidate):
                pagination_urls = self._generate_pagination_urls(candidate)
                for pag_url in pagination_urls:
                    pagination_candidates.add(pag_url)

        for pag_url in pagination_candidates:
            sourced_candidates[pag_url] = ("pagination", 0.4)

        # process all candidates with their provenance
        for candidate, (source_type, confidence) in sourced_candidates.items():
            results = self._process_candidate_url(
                candidate, source_url, current_depth, source_type, confidence
            )
            if not results:
                continue

            self.dynamic_urls_found += 1
            if self._looks_like_api_endpoint(candidate):
                self.api_endpoints_found += 1

            for result in results:
                yield result

    def _process_candidate_url(
        self,
        candidate_url: str,
        source_url: str,
        current_depth: int,
        discovery_source: str = "html_link",
        confidence: float = 1.0,
    ) -> list:
        """process URLs and pretend we're being efficient"""

        try:
            canonical_url = canonicalize_url_simple(candidate_url)
        except Exception as exc:
            self.logger.debug(f"Failed to canonicalize candidate {candidate_url}: {exc}")
            return []

        if canonical_url in self.seen_urls:
            self.duplicates_skipped += 1
            return []

        self.seen_urls.add(canonical_url)
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
                        'first_seen': discovery_time
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
        if hostname and not hostname.endswith('uconn.edu'):
            return None

        return absolute

    def _extract_urls_from_json_text(self, raw_value: str, response: Response) -> set:
        """Parse JSON strings and gather plausible URL values recursively."""

        stripped = raw_value.strip().rstrip(';')

        if not stripped:
            return set()

        try:
            payload = json.loads(stripped)
        except Exception:
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
                        if 'uconn.edu' not in value and '/api/' not in value and '.json' not in value:
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
        self.logger.info("=" * 80)
        self.logger.info("DISCOVERY SPIDER CRAWL SUMMARY")
        self.logger.info("=" * 80)

        # Overall statistics
        self.logger.info(f"Crawl completed: {reason}")
        self.logger.info(f"Total pages parsed: {self.total_urls_parsed:,}")
        self.logger.info(f"Unique URLs found: {self.unique_urls_found:,}")
        self.logger.info(f"Duplicates skipped: {self.duplicates_skipped:,}")
        self.logger.info(f"Seed URLs loaded: {self.seed_count:,}")
        self.logger.info(f"Sanitized seed URLs recovered: {self.sanitized_seed_count:,}")
        self.logger.info(f"Malformed seeds skipped: {self.malformed_seed_skipped:,}")
        self.logger.info(f"Dynamic/AJAX URLs discovered: {self.dynamic_urls_found:,}")
        self.logger.info(f"Hidden API endpoints discovered: {self.api_endpoints_found:,}")

        # Depth histogram for coverage analysis
        self.logger.info("-" * 40)
        self.logger.info("DEPTH DISTRIBUTION:")
        total_discovered = sum(self.depth_yields.values())
        for depth in sorted(self.depth_yields.keys()):
            count = self.depth_yields[depth]
            percentage = (count / total_discovered * 100) if total_discovered > 0 else 0
            self.logger.info(f"  Depth {depth}: {count:,} URLs ({percentage:.1f}%)")

        # Top referring pages (helps prune noisy seeds)
        self.logger.info("-" * 40)
        self.logger.info("TOP REFERRING PAGES (for pruning noisy seeds):")
        sorted_referrers = sorted(self.referring_pages.items(), key=lambda x: x[1], reverse=True)
        for i, (source_url, link_count) in enumerate(sorted_referrers[:10], 1):
            self.logger.info(f"  {i}. {link_count:,} links from {source_url}")

        # Efficiency metrics
        duplicate_rate = (self.duplicates_skipped / max(1, self.unique_urls_found + self.duplicates_skipped)) * 100
        self.logger.info("-" * 40)
        self.logger.info("EFFICIENCY METRICS:")
        self.logger.info(f"Duplicate rate: {duplicate_rate:.1f}% (lower is better)")
        self.logger.info(f"Discovery rate: {self.unique_urls_found / max(1, self.total_urls_parsed):.1f} URLs/page")

    def _clean_seed_url(self, raw_url: str, line_number: int) -> Tuple[Optional[str], bool]:
        """Attempt to clean malformed seed URLs while preserving usable entries."""

        url = raw_url.strip()
        if not url:
            return None, False

        sanitized = False
        lower_url = url.lower()

        # Remove stray wrapping quotes left over from CSV escapes.
        trimmed = url.strip("\"'")
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
                self.logger.warning(
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
            self.logger.warning(
                f"Skipping malformed seed URL at line {line_number}: {raw_url}"
            )
            return None, sanitized

        return url, sanitized

    def _generate_sitemap_requests(self) -> Iterator[scrapy.Request]:
        """Generate requests for common sitemap/robots locations because automation beats manual updates"""
        base_domains = ["uconn.edu", "www.uconn.edu"]

        for domain in base_domains:
            # check robots.txt first
            robots_url = f"https://{domain}/robots.txt"
            yield scrapy.Request(
                url=robots_url,
                callback=self._parse_robots,
                meta={'domain': domain, 'depth': 0, 'source_url': robots_url},
                dont_filter=True
            )

            # common sitemap locations
            sitemap_paths = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap.xml.gz"]
            for path in sitemap_paths:
                sitemap_url = f"https://{domain}{path}"
                yield scrapy.Request(
                    url=sitemap_url,
                    callback=self._parse_sitemap,
                    meta={'domain': domain, 'depth': 0, 'source_url': sitemap_url},
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
                if sitemap_url and 'uconn.edu' in sitemap_url:
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
            if 'uconn.edu' in nested_sitemap_url:
                yield scrapy.Request(
                    url=nested_sitemap_url,
                    callback=self._parse_sitemap,
                    meta=response.meta,
                    dont_filter=True
                )

        # extract actual URLs
        for url_match in url_pattern.finditer(response.text):
            url = url_match.group(1)
            try:
                canonical_url = canonicalize_url_simple(url)
                if canonical_url not in self.seen_urls:
                    self.seen_urls.add(canonical_url)
                    self.unique_urls_found += 1

                    yield DiscoveryItem(
                        source_url=source_url,
                        discovered_url=canonical_url,
                        first_seen=datetime.now().isoformat(),
                        discovery_depth=0,
                        discovery_source="sitemap",
                        confidence=0.95
                    )
            except Exception as e:
                self.logger.debug(f"Failed to process sitemap URL {url}: {e}")

    def _generate_pagination_urls(self, base_url: str) -> set:
        """Generate common pagination patterns for API endpoints"""
        from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

        pagination_urls = set()
        parsed = urlparse(base_url)
        query_params = parse_qs(parsed.query)

        # common pagination parameters to try
        pagination_patterns = [
            {'page': ['2', '3']},
            {'p': ['2', '3']},
            {'offset': ['10', '20']},
            {'start': ['10', '20']},
            {'limit': ['20', '50']},
            {'per_page': ['20', '50']}
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
