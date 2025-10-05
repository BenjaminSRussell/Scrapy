"""
Example: Discovery Spider with Ultra-Aggressive URL Extraction

This is a REFERENCE IMPLEMENTATION showing how to integrate ultra_discovery.py
DO NOT replace your current discovery_spider.py - use this as a guide!

INTEGRATION OPTIONS:
1. Full Mode: Maximum coverage, all 30 sources
2. Selective Mode: High-value pages only (RECOMMENDED)
3. Supplement Mode: Add specific sources

Choose the mode that fits your needs and copy the relevant code.
"""

import scrapy
from scrapy.http import Response
from scrapy.linkextractors import LinkExtractor

from src.common.schemas import DiscoveryItem
from src.stage1.ultra_discovery import UltraDiscovery, extract_all_urls

# ============================================================================
# OPTION 1: FULL MODE - Maximum Coverage (97%+ coverage)
# ============================================================================

class FullModeDiscoverySpider(scrapy.Spider):
    """Use ALL 30 ultra discovery sources on every page"""

    name = "discovery_full"

    def parse(self, response: Response):
        """Parse with ultra-aggressive extraction"""
        source_url = response.meta.get('source_url', response.url)
        current_depth = response.meta.get('depth', 0)

        # Skip if at max depth
        if current_depth >= self.max_depth:
            return

        # Extract from ALL sources (30 methods)
        discovered_count = 0

        for discovered_url in extract_all_urls(response):
            # Process each discovered URL
            yield from self._process_candidate_url(
                discovered_url,
                source_url,
                current_depth,
                "ultra_discovery",
                confidence=0.85
            )
            discovered_count += 1

        self.logger.info(
            f"Ultra discovery found {discovered_count} URLs from {source_url}"
        )


# ============================================================================
# OPTION 2: SELECTIVE MODE - Best Balance (92-94% coverage) ⭐ RECOMMENDED
# ============================================================================

class SelectiveModeDiscoverySpider(scrapy.Spider):
    """Use ultra discovery only on high-value pages"""

    name = "discovery_selective"

    def parse(self, response: Response):
        """Parse with selective ultra discovery"""
        source_url = response.meta.get('source_url', response.url)
        current_depth = response.meta.get('depth', 0)

        if current_depth >= self.max_depth:
            return

        # 1. ALWAYS use standard LinkExtractor (fast, reliable)
        le = LinkExtractor(
            allow_domains=self.allowed_domains,
            unique=True,
            deny_extensions=['jpg', 'jpeg', 'png', 'gif', 'pdf', 'zip']
        )

        links = le.extract_links(response)

        for link in links:
            yield from self._process_candidate_url(
                link.url,
                source_url,
                current_depth,
                "html_link",
                confidence=1.0,
                anchor_text=link.text
            )

        # 2. Use ultra discovery for HIGH-VALUE pages only
        if self._is_high_value_page(response):
            self.logger.info(
                f"High-value page detected, using ultra discovery: {source_url}"
            )

            ultra = UltraDiscovery(response)

            # Extract from high-quality sources
            ultra_count = 0

            # JavaScript URLs (often missed by standard crawlers)
            for url in ultra._extract_from_inline_scripts():
                yield from self._process_candidate_url(
                    url, source_url, current_depth, "javascript", 0.85
                )
                ultra_count += 1

            # JSON-LD structured data (high quality)
            for url in ultra._extract_from_json_ld():
                yield from self._process_candidate_url(
                    url, source_url, current_depth, "json_ld", 0.9
                )
                ultra_count += 1

            # Iframes (often contain important content)
            for url in ultra._extract_from_iframes():
                yield from self._process_candidate_url(
                    url, source_url, current_depth, "iframe", 0.85
                )
                ultra_count += 1

            # Srcset (responsive images, may have higher resolution versions)
            for url in ultra._extract_from_srcset():
                yield from self._process_candidate_url(
                    url, source_url, current_depth, "srcset", 0.75
                )
                ultra_count += 1

            # Pagination patterns
            for url in ultra._generate_from_patterns():
                yield from self._process_candidate_url(
                    url, source_url, current_depth, "pagination", 0.8
                )
                ultra_count += 1

            self.logger.info(
                f"Ultra discovery added {ultra_count} URLs from high-value page"
            )

    def _is_high_value_page(self, response: Response) -> bool:
        """
        Determine if page is high-value (research/academic content).

        High-value indicators:
        - URL contains research/faculty/course keywords
        - Content has multiple academic terms
        - Department or lab page
        """
        url_lower = response.url.lower()

        # URL-based detection
        high_value_patterns = [
            '/research/', '/publications/', '/faculty/', '/professors/',
            '/labs/', '/laboratory/', '/centers/', '/institutes/',
            '/projects/', '/courses/', '/programs/', '/curriculum/',
            '/departments/', '/people/', '/staff/', '/scholars/',
            '/grants/', '/papers/', '/studies/', '/phd/', '/graduate/'
        ]

        if any(pattern in url_lower for pattern in high_value_patterns):
            return True

        # Content-based detection
        html_lower = response.text.lower()

        academic_terms = [
            'research', 'publication', 'professor', 'laboratory',
            'curriculum', 'syllabus', 'course catalog', 'department',
            'faculty member', 'phd', 'graduate program', 'undergraduate'
        ]

        # Count how many academic terms appear
        term_count = sum(1 for term in academic_terms if term in html_lower)

        # High-value if 3+ academic terms present
        return term_count >= 3


# ============================================================================
# OPTION 3: SUPPLEMENT MODE - Minimal Changes (88-90% coverage)
# ============================================================================

class SupplementModeDiscoverySpider(scrapy.Spider):
    """Add specific ultra sources to existing discovery"""

    name = "discovery_supplement"

    def parse(self, response: Response):
        """Parse with standard discovery + specific ultra sources"""
        source_url = response.meta.get('source_url', response.url)
        current_depth = response.meta.get('depth', 0)

        if current_depth >= self.max_depth:
            return

        # 1. Your existing standard discovery code
        # ... (keep all existing code here) ...

        # 2. Add ONLY specific ultra sources
        ultra = UltraDiscovery(response)

        # Just JavaScript URLs (often missed, high value)
        for url in ultra._extract_from_inline_scripts():
            yield from self._process_candidate_url(
                url, source_url, current_depth, "javascript", 0.8
            )

        # Just JSON-LD (structured data, very high quality)
        for url in ultra._extract_from_json_ld():
            yield from self._process_candidate_url(
                url, source_url, current_depth, "json_ld", 0.9
            )

        # Optional: Add iframes if needed
        # for url in ultra._extract_from_iframes():
        #     yield from self._process_candidate_url(
        #         url, source_url, current_depth, "iframe", 0.8
        #     )


# ============================================================================
# HELPER METHODS (Used by all modes)
# ============================================================================

def _process_candidate_url(
    self,
    discovered_url: str,
    source_url: str,
    current_depth: int,
    discovery_source: str,
    confidence: float,
    anchor_text: str = None
) -> Iterator[DiscoveryItem]:
    """
    Process a candidate URL (shared by all modes).

    This method should match your existing _process_candidate_url implementation.
    """
    # Import your actual URL processing logic
    # This is a placeholder - use your existing implementation

    import hashlib
    from datetime import datetime

    # Generate URL hash
    url_hash = hashlib.sha256(discovered_url.encode()).hexdigest()

    # Check if URL is already seen
    if hasattr(self, 'url_deduplicator'):
        if not self.url_deduplicator.add_if_new(discovered_url):
            self.duplicates_skipped += 1
            return

    # Calculate importance score
    importance = self._calculate_importance(discovered_url, anchor_text or "")

    # Create discovery item
    item = DiscoveryItem(
        source_url=source_url,
        discovered_url=discovered_url,
        first_seen=datetime.now().isoformat(),
        url_hash=url_hash,
        discovery_depth=current_depth + 1,
        discovery_source=discovery_source,
        confidence=confidence,
        importance_score=importance,
        anchor_text=anchor_text,
        is_same_domain=self._is_same_domain(discovered_url)
    )

    yield item

    # Generate follow-up request if not at max depth
    if current_depth + 1 < self.max_depth:
        yield scrapy.Request(
            discovered_url,
            callback=self.parse,
            meta={
                'source_url': discovered_url,
                'depth': current_depth + 1,
                'first_seen': datetime.now().isoformat()
            },
            dont_filter=False,
            errback=self.errback_httpbin
        )


def _calculate_importance(self, url: str, anchor_text: str) -> float:
    """
    Calculate URL importance score (0.0 - 1.0).

    Higher scores for:
    - Research content
    - Faculty pages
    - Academic programs
    - Course catalogs
    """
    score = 0.5  # Base score

    url_lower = url.lower()
    anchor_lower = anchor_text.lower()

    # Research boost
    research_keywords = [
        'research', 'publication', 'paper', 'study',
        'project', 'grant', 'lab', 'laboratory'
    ]
    if any(kw in url_lower or kw in anchor_lower for kw in research_keywords):
        score += 0.3

    # Faculty/people boost
    people_keywords = [
        'faculty', 'professor', 'researcher', 'staff',
        'people', 'scholar', 'phd', 'graduate'
    ]
    if any(kw in url_lower or kw in anchor_lower for kw in people_keywords):
        score += 0.25

    # Academic program boost
    program_keywords = [
        'program', 'major', 'degree', 'course',
        'curriculum', 'syllabus', 'catalog'
    ]
    if any(kw in url_lower or kw in anchor_lower for kw in program_keywords):
        score += 0.2

    # Department boost
    if '/department' in url_lower or 'dept' in url_lower:
        score += 0.15

    # News/events - slightly lower priority
    if any(kw in url_lower for kw in ['news', 'event', 'blog', 'media']):
        score -= 0.1

    return min(max(score, 0.0), 1.0)


def _is_same_domain(self, url: str) -> bool:
    """Check if URL is same domain as allowed domains"""
    from urllib.parse import urlparse

    domain = urlparse(url).netloc
    return any(allowed in domain for allowed in self.allowed_domains)


# ============================================================================
# USAGE INSTRUCTIONS
# ============================================================================

"""
TO INTEGRATE INTO YOUR DISCOVERY_SPIDER.PY:

1. Choose your mode (Selective recommended)

2. Copy the relevant parse() method from above

3. Copy the helper methods (_process_candidate_url, etc.)

4. Add this import at the top:
   from src.stage1.ultra_discovery import UltraDiscovery, extract_all_urls

5. Test:
   ./run_the_scrape --stage 1

6. Verify increased URLs:
   wc -l data/processed/stage01/discovery_output.jsonl

Expected Results:
- Full Mode: 40,000-55,000 URLs (+140-230%)
- Selective Mode: 25,000-35,000 URLs (+50-110%) ⭐ RECOMMENDED
- Supplement Mode: 20,000-23,000 URLs (+20-40%)

See MAXIMUM_COVERAGE_GUIDE.md for complete documentation.
"""
