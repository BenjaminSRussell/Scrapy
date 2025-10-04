"""Adaptive depth configuration for intelligent crawling.

Learns which sections/subdomains are content-rich and adjusts depth accordingly.
"""

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any  # noqa: F401
from urllib.parse import urlparse

from .keyword_manager import KeywordManager

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
    last_updated: str = field(
        default_factory=lambda: datetime.now().isoformat()
    )

    # Quality indicators
    has_valuable_content: bool = False
    content_density: float = 0.0  # validated/discovered ratio
    avg_links_per_page: float = 0.0

    def __post_init__(self):
        self.keyword_manager = KeywordManager()

    def update_stats(
        self,
        discovered: int = 0,
        validated: int = 0,
        content_pages: int = 0,
        avg_words: int = 0,
        depth_reached: int = 0,
        valuable_content_thresholds: dict | None = None,
    ):
        """Update section statistics"""
        self.total_urls_discovered += discovered
        self.total_urls_validated += validated
        self.total_content_pages += content_pages

        # Update averages
        if avg_words > 0 and content_pages > 0:
            # Correctly calculate the new average
            total_pages_before = self.total_content_pages - content_pages
            if total_pages_before > 0:
                total_words = ((self.avg_word_count * total_pages_before) +
                              (avg_words * content_pages))
                self.avg_word_count = int(
                    total_words / self.total_content_pages
                )
            else:
                self.avg_word_count = avg_words
        
        # Update max useful depth
        if content_pages > 0 and depth_reached > self.max_useful_depth:
            self.max_useful_depth = depth_reached

        # Calculate content density
        if self.total_urls_discovered > 0:
            self.content_density = (
                self.total_urls_validated / self.total_urls_discovered
            )

        # Determine if section has valuable content
        thresholds = valuable_content_thresholds or {
            'pages': 10, 'words': 200, 'density': 0.3
        }
        self.has_valuable_content = (
            self.total_content_pages > thresholds['pages'] and
            self.avg_word_count > thresholds['words'] and
            self.content_density > thresholds['density']
        )

        self.last_updated = datetime.now().isoformat()

    def calculate_recommended_depth(
        self,
        base_depth: int = 3,
        max_depth: int = 8,
    ) -> int:
        """Calculate recommended crawl depth for this section.
        
        Factors are weighted to avoid aggressive depth increases.
        """
        keywords = self.keyword_manager.get_keywords()

        recommended = base_depth

        # Content density bonus (capped at +2)
        if self.content_density > 0.8:
            recommended += 2
        elif self.content_density > 0.6:
            recommended += 1

        # Word count bonus (capped at +1)
        if self.avg_word_count > 1500:
            recommended += 1
        # Avoid penalizing new sections
        elif self.avg_word_count < 100 and self.total_content_pages > 10:
            recommended -= 1

        # Content volume bonus (capped at +2)
        if self.total_content_pages > 500:
            recommended += 2
        elif self.total_content_pages > 100:
            recommended += 1

        # Keyword relevance bonus (capped at +2)
        if keywords:
            section_words = set(
                re.split(r'[-_//\s.]', self.section_pattern.lower())
            )
            keyword_matches = sum(1 for kw in keywords if kw in section_words)
            
            if keyword_matches >= 3:
                recommended += 2  # Strong relevance
            elif keyword_matches >= 1:
                recommended += 1  # Some relevance

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
    This class is thread-safe.
    """

    def __init__(
        self,
        config_file: Path,
        base_depth: int = 3,
        max_depth: int = 8,
        valuable_content_thresholds: dict | None = None,
    ):
        self.config_file = Path(config_file)
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.base_depth = base_depth
        self.max_depth = max_depth
        self.valuable_content_thresholds = valuable_content_thresholds or {
            'pages': 10, 'words': 200, 'density': 0.3
        }

        self.section_stats: dict[str, SectionStats] = {}
        self._lock = threading.Lock()

        self._load_config()
        logger.info(f"AdaptiveDepthManager initialized for {config_file}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.save_config()

    def _load_config(self):
        """Load adaptive depth configuration from disk."""
        if not self.config_file.exists():
            logger.info(
                "No existing adaptive depth config found, starting fresh."
            )
            return

        try:
            with self.config_file.open('r', encoding='utf-8') as f:
                data = json.load(f)
            for section, stats_dict in data.get('sections', {}).items():
                self.section_stats[section] = SectionStats(**stats_dict)
            logger.info(
                f"Loaded adaptive depth config with "
                f"{len(self.section_stats)} sections."
            )
        except json.JSONDecodeError as e:
            logger.error(
                f"Failed to decode config file {self.config_file}: {e}. "
                "Backing it up and starting fresh."
            )
            try:
                backup_path = self.config_file.with_suffix(
                    f".corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                self.config_file.rename(backup_path)
                logger.info(f"Corrupt config moved to {backup_path}")
            except OSError as rename_error:
                logger.error(
                    f"Could not back up corrupt config file: {rename_error}"
                )
        except Exception as e:
            logger.error(
                f"Failed to load adaptive depth config: {e}", 
                exc_info=True
            )

    def save_config(self):
        """Persist adaptive depth configuration atomically."""
        suffix = self.config_file.suffix
        lock_file = self.config_file.with_suffix(suffix + '.lock')
        try:
            with self._lock:
                # Use a lock file for cross-process safety
                try:
                    with open(lock_file, 'x'):
                        pass
                except FileExistsError:
                    logger.warning(
                        f"Config file is locked by another process ({lock_file}), "
                        "skipping save."
                    )
                    return

                data = {
                    'metadata': {
                        'base_depth': self.base_depth,
                        'max_depth': self.max_depth,
                        'last_updated': datetime.now().isoformat(),
                        'total_sections': len(self.section_stats)
                    },
                    'sections': {
                        k: asdict(v) for k, v in self.section_stats.items()
                    },
                }
                
                # Write to a temporary file first
                suffix = self.config_file.suffix
                temp_file = self.config_file.with_suffix(suffix + '.tmp')
                with temp_file.open('w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)
                
                # Atomic rename
                os.replace(temp_file, self.config_file)
                logger.info(f"Saved adaptive depth config to {self.config_file}")

        except Exception as e:
            logger.error(f"Failed to save adaptive depth config: {e}", exc_info=True)
        finally:
            # Clean up lock file
            if lock_file.exists():
                lock_file.unlink()

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        """Basic URL validation."""
        try:
            parsed = urlparse(url)
            return all([parsed.scheme in ['http', 'https'], parsed.netloc])
        except (ValueError, AttributeError):
            return False

    def extract_section(self, url: str) -> str:
        """
        Extract a section identifier from a URL.
        - Subdomains (except 'www') become sections (e.g., 'events.uconn.edu').
        - Root domains and 'www' use the first path segment (e.g., 'uconn.edu/admissions').
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        path = parsed.path.strip('/')

        if domain.startswith('www.'):
            domain = domain[4:]

        subdomain = domain.split('.')[0] if domain.count('.') > 1 else None

        if subdomain:
            return domain
        
        if path:
            first_segment = path.split('/')[0]
            return f"{domain}/{first_segment}"

        return domain

    def get_depth_for_url(self, url: str) -> int:
        """Get recommended crawl depth for a URL. Thread-safe."""
        if not self._is_valid_url(url):
            logger.debug(f"Skipping depth calculation for invalid URL: {url}")
            return self.base_depth

        section = self.extract_section(url)
        
        with self._lock:
            stats = self.section_stats.get(section)

        if stats:
            depth = stats.calculate_recommended_depth(self.base_depth, self.max_depth)
            logger.debug(f"Adaptive depth for {section}: {depth} (density: {stats.content_density:.2%}, words: {stats.avg_word_count})")
            return depth

        return self.base_depth

    def record_discovery(self, url: str, depth: int):
        """Record a discovered URL. Thread-safe."""
        if not self._is_valid_url(url):
            return
        
        section = self.extract_section(url)

        with self._lock:
            if section not in self.section_stats:
                self.section_stats[section] = SectionStats(section_pattern=section)
            
            stats = self.section_stats[section]
            stats.update_stats(discovered=1, depth_reached=depth)

    def record_validation(self, url: str, is_valid: bool, has_content: bool = False,
                         word_count: int = 0, depth: int = 0):
        """Record validation results for a URL. Thread-safe."""
        if not self._is_valid_url(url):
            return

        section = self.extract_section(url)

        with self._lock:
            if section not in self.section_stats:
                self.section_stats[section] = SectionStats(section_pattern=section)

            stats = self.section_stats[section]
            validated = 1 if is_valid else 0
            content_pages = 1 if (is_valid and has_content) else 0

            stats.update_stats(
                validated=validated,
                content_pages=content_pages,
                avg_words=word_count,
                depth_reached=depth,
                valuable_content_thresholds=self.valuable_content_thresholds
            )

    def get_high_value_sections(self, min_content_pages: int = 50) -> list[str]:
        """Get sections identified as having valuable content."""
        with self._lock:
            # Create a copy to avoid issues with modification during iteration
            stats_copy = list(self.section_stats.items())

        high_value = [
            section for section, stats in stats_copy
            if stats.has_valuable_content and stats.total_content_pages >= min_content_pages
        ]
        
        return sorted(high_value, key=lambda s: self.section_stats[s].total_content_pages, reverse=True)

    def get_low_value_sections(self, max_content_density: float = 0.1) -> list[str]:
        """Get sections with low content value."""
        with self._lock:
            stats_copy = list(self.section_stats.items())

        low_value = [
            section for section, stats in stats_copy
            if stats.total_urls_discovered > 20 and stats.content_density < max_content_density
        ]

        return sorted(low_value, key=lambda s: self.section_stats[s].content_density)

    def print_report(self):
        """Print a human-readable report of adaptive depth statistics."""
        logger.info("=" * 80)
        logger.info("ADAPTIVE DEPTH REPORT")
        logger.info("=" * 80)

        with self._lock:
            stats_values = list(self.section_stats.values())
            high_value = self.get_high_value_sections(min_content_pages=20)
            low_value = self.get_low_value_sections(max_content_density=0.2)

            if high_value:
                logger.info(f"\nHigh-Value Sections ({len(high_value)}) - Recommended Deeper Crawling:")
                for section in high_value[:10]:
                    stats = self.section_stats[section]
                    logger.info(
                        f"  {section:40s}: depth={stats.current_recommended_depth} "
                        f"(pages: {stats.total_content_pages:4d}, "
                        f"words: {stats.avg_word_count:5d}, "
                        f"density: {stats.content_density:5.1%})"
                    )

            if low_value:
                logger.info(f"\nLow-Value Sections ({len(low_value)}) - Shallow Crawling Sufficient:")
                for section in low_value[:10]:
                    stats = self.section_stats[section]
                    logger.info(
                        f"  {section:40s}: depth={stats.current_recommended_depth} "
                        f"(discovered: {stats.total_urls_discovered:4d}, "
                        f"valid: {stats.total_urls_validated:4d}, "
                        f"density: {stats.content_density:5.1%})"
                    )

            total_sections = len(stats_values)
            if total_sections > 0:
                avg_depth = sum(s.current_recommended_depth for s in stats_values) / total_sections
                deep_sections = sum(1 for s in stats_values if s.current_recommended_depth > self.base_depth)
                shallow_sections = sum(1 for s in stats_values if s.current_recommended_depth < self.base_depth)

                logger.info("\nOverall Statistics:")
                logger.info(f"  Total sections tracked: {total_sections}")
                logger.info(f"  Average recommended depth: {avg_depth:.1f}")
                logger.info(f"  Sections with deeper crawling: {deep_sections}")
                logger.info(f"  Sections with shallow crawling: {shallow_sections}")
            
            logger.info(f"  Base depth: {self.base_depth}, Max depth: {self.max_depth}")

        logger.info("=" * 80)

    def get_depth_configuration(self) -> dict[str, int]:
        """Get a dictionary mapping sections to their recommended depths."""
        with self._lock:
            return {
                section: stats.current_recommended_depth
                for section, stats in self.section_stats.items()
            }

    def suggest_depth_adjustments(self) -> dict[str, list]:
        """Suggest depth adjustments based on current data."""
        suggestions = {
            'increase_depth': [],
            'decrease_depth': [],
            'maintain': []
        }
        with self._lock:
            stats_copy = list(self.section_stats.items())

        for section, stats in stats_copy:
            new_depth = stats.current_recommended_depth
            # Assuming base_depth is the old_depth for comparison
            if new_depth > self.base_depth:
                suggestions['increase_depth'].append({
                    'section': section, 'from': self.base_depth, 'to': new_depth,
                    'reason': f"High content value (density: {stats.content_density:.1%}, pages: {stats.total_content_pages})"
                })
            elif new_depth < self.base_depth:
                suggestions['decrease_depth'].append({
                    'section': section, 'from': self.base_depth, 'to': new_depth,
                    'reason': f"Low content value (density: {stats.content_density:.1%})"
                })
            else:
                suggestions['maintain'].append({'section': section, 'depth': new_depth})
        
        return suggestions