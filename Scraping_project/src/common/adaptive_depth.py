"""
Adaptive depth configuration for intelligent crawling.
Learns which sections/subdomains are content-rich and adjusts depth accordingly.
"""

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse
import re

logger = logging.getLogger(__name__)


@dataclass
class SectionStats:
    """Statistics for a specific section or subdomain"""

    section_pattern: str
    total_urls_discovered: int = 0
    total_urls_validated: int = 0
    total_content_pages: int = 0
    avg_content_quality: float = 0.0
    avg_word_count: int = 0
    max_useful_depth: int = 0
    current_recommended_depth: int = 3
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    # Quality indicators
    has_valuable_content: bool = False
    content_density: float = 0.0  # validated/discovered ratio
    avg_links_per_page: float = 0.0

    def update_stats(self, discovered: int = 0, validated: int = 0, content_pages: int = 0,
                     avg_words: int = 0, depth_reached: int = 0):
        """Update section statistics"""
        self.total_urls_discovered += discovered
        self.total_urls_validated += validated
        self.total_content_pages += content_pages

        # Update averages
        if avg_words > 0:
            total_words = (self.avg_word_count * (self.total_content_pages - content_pages) +
                          avg_words * content_pages)
            self.avg_word_count = int(total_words / self.total_content_pages) if self.total_content_pages > 0 else 0

        # Update max useful depth
        if content_pages > 0 and depth_reached > self.max_useful_depth:
            self.max_useful_depth = depth_reached

        # Calculate content density
        if self.total_urls_discovered > 0:
            self.content_density = self.total_urls_validated / self.total_urls_discovered

        # Determine if section has valuable content
        self.has_valuable_content = (
            self.total_content_pages > 10 and
            self.avg_word_count > 200 and
            self.content_density > 0.3
        )

        self.last_updated = datetime.now().isoformat()

    def calculate_recommended_depth(self, base_depth: int = 3, max_depth: int = 8) -> int:
        """
        Calculate recommended crawl depth for this section.

        Factors:
        - Content density (high = deeper)
        - Average word count (high = deeper)
        - Historical max useful depth
        - Content page count
        """
        recommended = base_depth

        # High content density bonus (+1 or +2 depth)
        if self.content_density > 0.7:
            recommended += 2
        elif self.content_density > 0.5:
            recommended += 1

        # High word count bonus
        if self.avg_word_count > 1000:
            recommended += 1
        elif self.avg_word_count < 100:
            recommended -= 1

        # Many content pages found
        if self.total_content_pages > 100:
            recommended += 1
        elif self.total_content_pages > 500:
            recommended += 2

        # Use historical max if we've found content deep
        if self.max_useful_depth > recommended:
            recommended = min(self.max_useful_depth + 1, max_depth)

        # Clamp to reasonable range
        recommended = max(1, min(recommended, max_depth))

        self.current_recommended_depth = recommended
        return recommended


class AdaptiveDepthManager:
    """
    Manages adaptive depth configuration per section/subdomain.
    Learns from crawl history to optimize depth settings.
    """

    def __init__(self, config_file: Path, base_depth: int = 3, max_depth: int = 8):
        self.config_file = Path(config_file)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.base_depth = base_depth
        self.max_depth = max_depth

        # Section statistics
        self.section_stats: Dict[str, SectionStats] = {}

        # Load existing configuration
        self._load_config()

        logger.info(f"AdaptiveDepthManager initialized: {config_file}")

    def _load_config(self):
        """Load adaptive depth configuration from disk"""
        if not self.config_file.exists():
            logger.info("No existing adaptive depth config found, starting fresh")
            return

        try:
            with self.config_file.open('r') as f:
                data = json.load(f)

                for section, stats_dict in data.get('sections', {}).items():
                    self.section_stats[section] = SectionStats(**stats_dict)

            logger.info(f"Loaded adaptive depth config: {len(self.section_stats)} sections")

        except Exception as e:
            logger.error(f"Failed to load adaptive depth config: {e}")

    def save_config(self):
        """Persist adaptive depth configuration"""
        try:
            data = {
                'sections': {k: asdict(v) for k, v in self.section_stats.items()},
                'metadata': {
                    'base_depth': self.base_depth,
                    'max_depth': self.max_depth,
                    'last_updated': datetime.now().isoformat(),
                    'total_sections': len(self.section_stats)
                }
            }

            with self.config_file.open('w') as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved adaptive depth config to {self.config_file}")

        except Exception as e:
            logger.error(f"Failed to save adaptive depth config: {e}")

    def extract_section(self, url: str) -> str:
        """
        Extract section identifier from URL.

        Examples:
            https://catalog.uconn.edu/courses -> catalog.uconn.edu/courses
            https://events.uconn.edu/2024 -> events.uconn.edu
            https://www.uconn.edu/faculty/bio -> www.uconn.edu/faculty
        """
        parsed = urlparse(url)
        domain = parsed.netloc
        path = parsed.path.strip('/')

        # For subdomains, use subdomain as section
        if domain.count('.') > 1:  # e.g., catalog.uconn.edu
            return domain

        # For main domain, use first path segment
        if path:
            first_segment = path.split('/')[0]
            return f"{domain}/{first_segment}"

        return domain

    def get_depth_for_url(self, url: str) -> int:
        """
        Get recommended crawl depth for a URL based on its section.

        Args:
            url: The URL to get depth for

        Returns:
            Recommended depth (1 to max_depth)
        """
        section = self.extract_section(url)

        if section in self.section_stats:
            stats = self.section_stats[section]
            depth = stats.calculate_recommended_depth(self.base_depth, self.max_depth)
            logger.debug(
                f"Adaptive depth for {section}: {depth} "
                f"(density: {stats.content_density:.2%}, words: {stats.avg_word_count})"
            )
            return depth

        # No history for this section, use base depth
        return self.base_depth

    def record_discovery(self, url: str, depth: int):
        """Record that a URL was discovered at a certain depth"""
        section = self.extract_section(url)

        if section not in self.section_stats:
            self.section_stats[section] = SectionStats(section_pattern=section)

        stats = self.section_stats[section]
        stats.update_stats(discovered=1, depth_reached=depth)

    def record_validation(self, url: str, is_valid: bool, has_content: bool = False,
                         word_count: int = 0, depth: int = 0):
        """Record validation results for adaptive learning"""
        section = self.extract_section(url)

        if section not in self.section_stats:
            self.section_stats[section] = SectionStats(section_pattern=section)

        stats = self.section_stats[section]

        validated = 1 if is_valid else 0
        content_pages = 1 if (is_valid and has_content) else 0

        stats.update_stats(
            validated=validated,
            content_pages=content_pages,
            avg_words=word_count,
            depth_reached=depth
        )

    def get_high_value_sections(self, min_content_pages: int = 50) -> List[str]:
        """Get sections that have valuable content"""
        high_value = []

        for section, stats in self.section_stats.items():
            if stats.has_valuable_content and stats.total_content_pages >= min_content_pages:
                high_value.append(section)

        return sorted(high_value, key=lambda s: self.section_stats[s].total_content_pages, reverse=True)

    def get_low_value_sections(self, max_content_density: float = 0.1) -> List[str]:
        """Get sections with low content value (can use shallow depth)"""
        low_value = []

        for section, stats in self.section_stats.items():
            if stats.total_urls_discovered > 20 and stats.content_density < max_content_density:
                low_value.append(section)

        return sorted(low_value, key=lambda s: self.section_stats[s].content_density)

    def print_report(self):
        """Print human-readable adaptive depth report"""
        logger.info("=" * 80)
        logger.info("ADAPTIVE DEPTH REPORT")
        logger.info("=" * 80)

        # High-value sections (deserve deeper crawling)
        high_value = self.get_high_value_sections(min_content_pages=20)
        if high_value:
            logger.info(f"\nHigh-Value Sections ({len(high_value)}) - Recommended Deeper Crawling:")
            for section in high_value[:10]:  # Top 10
                stats = self.section_stats[section]
                logger.info(
                    f"  {section:40s}: depth={stats.current_recommended_depth} "
                    f"(pages: {stats.total_content_pages:4d}, "
                    f"words: {stats.avg_word_count:5d}, "
                    f"density: {stats.content_density:5.1%})"
                )

        # Low-value sections (can use shallow depth)
        low_value = self.get_low_value_sections(max_content_density=0.2)
        if low_value:
            logger.info(f"\nLow-Value Sections ({len(low_value)}) - Shallow Crawling Sufficient:")
            for section in low_value[:10]:  # Bottom 10
                stats = self.section_stats[section]
                logger.info(
                    f"  {section:40s}: depth={stats.current_recommended_depth} "
                    f"(discovered: {stats.total_urls_discovered:4d}, "
                    f"valid: {stats.total_urls_validated:4d}, "
                    f"density: {stats.content_density:5.1%})"
                )

        # Overall statistics
        total_sections = len(self.section_stats)
        avg_depth = sum(s.current_recommended_depth for s in self.section_stats.values()) / total_sections if total_sections > 0 else 0
        deep_sections = sum(1 for s in self.section_stats.values() if s.current_recommended_depth > self.base_depth)
        shallow_sections = sum(1 for s in self.section_stats.values() if s.current_recommended_depth < self.base_depth)

        logger.info(f"\nOverall Statistics:")
        logger.info(f"  Total sections tracked: {total_sections}")
        logger.info(f"  Average recommended depth: {avg_depth:.1f}")
        logger.info(f"  Sections with deeper crawling: {deep_sections}")
        logger.info(f"  Sections with shallow crawling: {shallow_sections}")
        logger.info(f"  Base depth: {self.base_depth}, Max depth: {self.max_depth}")

        logger.info("=" * 80)

    def get_depth_configuration(self) -> Dict[str, int]:
        """Get a dictionary mapping sections to recommended depths"""
        return {
            section: stats.current_recommended_depth
            for section, stats in self.section_stats.items()
        }

    def suggest_depth_adjustments(self) -> Dict[str, Dict[str, any]]:
        """
        Suggest depth adjustments based on current data.

        Returns:
            Dict with 'increase' and 'decrease' recommendations
        """
        suggestions = {
            'increase_depth': [],
            'decrease_depth': [],
            'maintain': []
        }

        for section, stats in self.section_stats.items():
            old_depth = self.base_depth
            new_depth = stats.current_recommended_depth

            if new_depth > old_depth:
                suggestions['increase_depth'].append({
                    'section': section,
                    'from': old_depth,
                    'to': new_depth,
                    'reason': f"High content density ({stats.content_density:.1%}), {stats.total_content_pages} pages found"
                })
            elif new_depth < old_depth:
                suggestions['decrease_depth'].append({
                    'section': section,
                    'from': old_depth,
                    'to': new_depth,
                    'reason': f"Low content density ({stats.content_density:.1%})"
                })
            else:
                suggestions['maintain'].append({
                    'section': section,
                    'depth': new_depth
                })

        return suggestions
