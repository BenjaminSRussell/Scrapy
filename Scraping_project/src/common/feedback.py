"""
Feedback system for tracking URL pattern quality across pipeline stages.
Stage 2 reports failed URL patterns back to Stage 1 for adaptive discovery.
"""

import json
import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


@dataclass
class URLPatternStats:
    """Statistics for a specific URL pattern or discovery source"""

    pattern: str
    discovery_source: str
    total_discovered: int = 0
    total_validated: int = 0
    total_failed: int = 0
    success_rate: float = 0.0
    avg_confidence: float = 0.0
    last_seen: str = field(default_factory=lambda: datetime.now().isoformat())
    failure_types: dict[str, int] = field(default_factory=dict)  # e.g., {"404": 10, "timeout": 2}

    def update_success_rate(self):
        """Recalculate success rate based on current stats"""
        total = self.total_validated + self.total_failed
        if total > 0:
            self.success_rate = self.total_validated / total
        else:
            self.success_rate = 0.0


@dataclass
class SessionStats:
    """Statistics for a specific crawl session"""

    session_id: str
    started_at: str
    completed_at: str | None = None
    total_discovered: int = 0
    total_validated: int = 0
    total_failed: int = 0
    success_rate: float = 0.0

    # Per-source breakdown for this session
    source_performance: dict[str, dict[str, int]] = field(default_factory=dict)


class FeedbackStore:
    """
    Stores and manages feedback from Stage 2 validation results.
    Used by Stage 1 to adapt discovery strategies.
    Tracks heuristic quality across crawl sessions.
    """

    def __init__(self, feedback_file: Path):
        self.feedback_file = Path(feedback_file)
        self.feedback_file.parent.mkdir(parents=True, exist_ok=True)

        # In-memory stats
        self.pattern_stats: dict[str, URLPatternStats] = {}
        self.source_stats: dict[str, URLPatternStats] = {}
        self.session_history: list[SessionStats] = []

        # Current session tracking
        self.current_session = SessionStats(
            session_id=f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            started_at=datetime.now().isoformat()
        )

        # Load existing feedback
        self._load_feedback()

        logger.info(f"FeedbackStore initialized: {feedback_file}")

    def _load_feedback(self):
        """Load feedback from persistent storage"""
        if not self.feedback_file.exists():
            logger.info("No existing feedback file found, starting fresh")
            return

        try:
            with self.feedback_file.open('r') as f:
                data = json.load(f)

                # Load pattern stats
                for pattern, stats_dict in data.get('patterns', {}).items():
                    self.pattern_stats[pattern] = URLPatternStats(**stats_dict)

                # Load source stats
                for source, stats_dict in data.get('sources', {}).items():
                    self.source_stats[source] = URLPatternStats(**stats_dict)

                # Load session history
                for session_dict in data.get('session_history', []):
                    self.session_history.append(SessionStats(**session_dict))

            logger.info(f"Loaded feedback: {len(self.pattern_stats)} patterns, {len(self.source_stats)} sources, {len(self.session_history)} sessions")

        except Exception as e:
            logger.error(f"Failed to load feedback: {e}")

    def save_feedback(self):
        """Persist feedback to disk"""
        try:
            # Finalize current session
            self.current_session.completed_at = datetime.now().isoformat()
            total = self.current_session.total_validated + self.current_session.total_failed
            if total > 0:
                self.current_session.success_rate = self.current_session.total_validated / total

            # Add current session to history
            self.session_history.append(self.current_session)

            # Keep only last 50 sessions to avoid file bloat
            if len(self.session_history) > 50:
                self.session_history = self.session_history[-50:]

            data = {
                'patterns': {k: asdict(v) for k, v in self.pattern_stats.items()},
                'sources': {k: asdict(v) for k, v in self.source_stats.items()},
                'session_history': [asdict(s) for s in self.session_history],
                'last_updated': datetime.now().isoformat(),
                'total_sessions': len(self.session_history)
            }

            with self.feedback_file.open('w') as f:
                json.dump(data, f, indent=2)

            logger.info(f"Saved feedback to {self.feedback_file} ({len(self.session_history)} sessions)")

        except Exception as e:
            logger.error(f"Failed to save feedback: {e}")

    def extract_url_pattern(self, url: str) -> str:
        """
        Extract a generalized pattern from a URL for tracking.

        Examples:
            /events/page/5 -> /events/page/{num}
            /api/data?id=123 -> /api/data?id={param}
            /dept/bio/faculty/smith -> /dept/{word}/faculty/{word}
        """
        parsed = urlparse(url)
        path = parsed.path

        # Replace numeric segments with {num}
        path = re.sub(r'/\d+', '/{num}', path)

        # Replace UUID-like segments
        path = re.sub(r'/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '/{uuid}', path, flags=re.IGNORECASE)

        # Replace long alphanumeric IDs (8+ chars)
        path = re.sub(r'/[a-zA-Z0-9]{8,}', '/{id}', path)

        # Simplify query parameters
        if parsed.query:
            params = parse_qs(parsed.query)
            simplified_params = []
            for key in sorted(params.keys()):
                # Classify param value type
                value = params[key][0] if params[key] else ''
                if value.isdigit():
                    simplified_params.append(f"{key}={{num}}")
                else:
                    simplified_params.append(f"{key}={{param}}")

            query_pattern = "&".join(simplified_params)
            path = f"{path}?{query_pattern}"

        return path

    def record_discovery(self, url: str, discovery_source: str, confidence: float):
        """Record that a URL was discovered in Stage 1"""
        pattern = self.extract_url_pattern(url)

        # Update pattern stats
        if pattern not in self.pattern_stats:
            self.pattern_stats[pattern] = URLPatternStats(
                pattern=pattern,
                discovery_source=discovery_source,
                avg_confidence=confidence
            )

        stats = self.pattern_stats[pattern]
        stats.total_discovered += 1
        # Running average for confidence
        stats.avg_confidence = (stats.avg_confidence * (stats.total_discovered - 1) + confidence) / stats.total_discovered
        stats.last_seen = datetime.now().isoformat()

        # Update source stats
        if discovery_source not in self.source_stats:
            self.source_stats[discovery_source] = URLPatternStats(
                pattern="*",
                discovery_source=discovery_source
            )

        self.source_stats[discovery_source].total_discovered += 1

    def record_validation(self, url: str, discovery_source: str, is_valid: bool,
                         status_code: int | None = None, error_type: str | None = None):
        """Record validation result from Stage 2"""
        pattern = self.extract_url_pattern(url)

        # Update current session stats
        if is_valid:
            self.current_session.total_validated += 1
        else:
            self.current_session.total_failed += 1

        # Track per-source performance in current session
        if discovery_source not in self.current_session.source_performance:
            self.current_session.source_performance[discovery_source] = {'validated': 0, 'failed': 0}

        if is_valid:
            self.current_session.source_performance[discovery_source]['validated'] += 1
        else:
            self.current_session.source_performance[discovery_source]['failed'] += 1

        # Update pattern stats
        if pattern in self.pattern_stats:
            stats = self.pattern_stats[pattern]

            if is_valid:
                stats.total_validated += 1
            else:
                stats.total_failed += 1
                # Track failure types
                if status_code:
                    failure_key = str(status_code)
                elif error_type:
                    failure_key = error_type
                else:
                    failure_key = "unknown"

                stats.failure_types[failure_key] = stats.failure_types.get(failure_key, 0) + 1

            stats.update_success_rate()
            stats.last_seen = datetime.now().isoformat()

        # Update source stats
        if discovery_source in self.source_stats:
            source_stats = self.source_stats[discovery_source]

            if is_valid:
                source_stats.total_validated += 1
            else:
                source_stats.total_failed += 1

            source_stats.update_success_rate()
            source_stats.last_seen = datetime.now().isoformat()

    def get_low_quality_patterns(self, min_samples: int = 10, max_success_rate: float = 0.3) -> list[str]:
        """
        Get URL patterns that consistently fail validation.

        Args:
            min_samples: Minimum number of attempts before considering pattern
            max_success_rate: Maximum success rate to be considered low quality

        Returns:
            List of low-quality URL patterns to avoid
        """
        low_quality = []

        for pattern, stats in self.pattern_stats.items():
            total_attempts = stats.total_validated + stats.total_failed

            if total_attempts >= min_samples and stats.success_rate <= max_success_rate:
                low_quality.append(pattern)
                logger.debug(f"Low quality pattern: {pattern} (success rate: {stats.success_rate:.2%})")

        return low_quality

    def get_source_quality(self, discovery_source: str) -> float:
        """Get success rate for a specific discovery source"""
        if discovery_source in self.source_stats:
            return self.source_stats[discovery_source].success_rate
        return 1.0  # Default to high quality if no data

    def should_throttle_source(self, discovery_source: str,
                               min_samples: int = 50,
                               max_success_rate: float = 0.4) -> bool:
        """
        Determine if a discovery source should be throttled.

        Args:
            discovery_source: The source to check (e.g., "ajax_endpoint", "json_blob")
            min_samples: Minimum samples before throttling
            max_success_rate: Maximum success rate before throttling kicks in

        Returns:
            True if source should be throttled/disabled
        """
        if discovery_source not in self.source_stats:
            return False

        stats = self.source_stats[discovery_source]
        total_attempts = stats.total_validated + stats.total_failed

        if total_attempts >= min_samples and stats.success_rate <= max_success_rate:
            logger.warning(
                f"Discovery source '{discovery_source}' should be throttled: "
                f"{stats.total_validated}/{total_attempts} success ({stats.success_rate:.2%})"
            )
            return True

        return False

    def get_adjusted_confidence(self, url: str, discovery_source: str, base_confidence: float) -> float:
        """
        Adjust confidence score based on historical pattern performance.

        Args:
            url: The discovered URL
            discovery_source: How it was discovered
            base_confidence: Original confidence score

        Returns:
            Adjusted confidence score (0.0 - 1.0)
        """
        pattern = self.extract_url_pattern(url)

        # Check pattern history
        if pattern in self.pattern_stats:
            stats = self.pattern_stats[pattern]
            total_attempts = stats.total_validated + stats.total_failed

            # Only adjust if we have enough samples
            if total_attempts >= 5:
                # Weight pattern success rate with base confidence
                adjusted = (base_confidence * 0.5) + (stats.success_rate * 0.5)

                if adjusted != base_confidence:
                    logger.debug(
                        f"Adjusted confidence for {pattern}: {base_confidence:.2f} -> {adjusted:.2f} "
                        f"(pattern success: {stats.success_rate:.2%})"
                    )

                return adjusted

        return base_confidence

    def get_heuristic_trends(self, num_sessions: int = 10) -> dict[str, list[float]]:
        """
        Get success rate trends for each heuristic across recent sessions.

        Args:
            num_sessions: Number of recent sessions to analyze

        Returns:
            Dict mapping source names to list of success rates over time
        """
        trends = defaultdict(list)

        # Analyze recent sessions
        recent_sessions = self.session_history[-num_sessions:] if len(self.session_history) > 0 else []

        for session in recent_sessions:
            for source, perf in session.source_performance.items():
                validated = perf.get('validated', 0)
                failed = perf.get('failed', 0)
                total = validated + failed

                success_rate = validated / total if total > 0 else 0.0
                trends[source].append(success_rate)

        return dict(trends)

    def get_improving_heuristics(self, min_sessions: int = 3) -> list[str]:
        """Get heuristics that are showing improvement over time"""
        trends = self.get_heuristic_trends()
        improving = []

        for source, rates in trends.items():
            if len(rates) >= min_sessions:
                # Check if trending upward (later sessions better than earlier)
                first_half_avg = sum(rates[:len(rates)//2]) / (len(rates)//2)
                second_half_avg = sum(rates[len(rates)//2:]) / (len(rates) - len(rates)//2)

                if second_half_avg > first_half_avg * 1.1:  # 10% improvement
                    improving.append(source)

        return improving

    def get_declining_heuristics(self, min_sessions: int = 3) -> list[str]:
        """Get heuristics that are showing decline over time"""
        trends = self.get_heuristic_trends()
        declining = []

        for source, rates in trends.items():
            if len(rates) >= min_sessions:
                # Check if trending downward
                first_half_avg = sum(rates[:len(rates)//2]) / (len(rates)//2)
                second_half_avg = sum(rates[len(rates)//2:]) / (len(rates) - len(rates)//2)

                if second_half_avg < first_half_avg * 0.9:  # 10% decline
                    declining.append(source)

        return declining

    def get_feedback_summary(self) -> dict[str, any]:
        """Get a summary of feedback statistics"""
        return {
            'total_patterns': len(self.pattern_stats),
            'total_sources': len(self.source_stats),
            'total_sessions': len(self.session_history),
            'low_quality_patterns': len(self.get_low_quality_patterns()),
            'current_session': {
                'session_id': self.current_session.session_id,
                'validated': self.current_session.total_validated,
                'failed': self.current_session.total_failed,
                'success_rate': self.current_session.success_rate
            },
            'source_performance': {
                source: {
                    'success_rate': stats.success_rate,
                    'total_discovered': stats.total_discovered,
                    'total_validated': stats.total_validated,
                    'total_failed': stats.total_failed
                }
                for source, stats in self.source_stats.items()
            }
        }

    def print_report(self):
        """Print a human-readable feedback report"""
        logger.info("=" * 60)
        logger.info("FEEDBACK REPORT")
        logger.info("=" * 60)

        # Source performance
        logger.info(f"\nDiscovery Source Performance ({len(self.source_stats)} sources):")
        for source, stats in sorted(self.source_stats.items(), key=lambda x: x[1].success_rate):
            total = stats.total_validated + stats.total_failed
            if total > 0:
                logger.info(
                    f"  {source:20s}: {stats.success_rate:6.2%} success "
                    f"({stats.total_validated:4d} valid / {total:4d} total)"
                )

        # Low quality patterns
        low_quality = self.get_low_quality_patterns(min_samples=5, max_success_rate=0.4)
        if low_quality:
            logger.info(f"\nLow Quality Patterns ({len(low_quality)}):")
            for pattern in low_quality[:10]:  # Show top 10
                stats = self.pattern_stats[pattern]
                total = stats.total_validated + stats.total_failed
                logger.info(
                    f"  {pattern:50s}: {stats.success_rate:6.2%} "
                    f"({stats.total_validated}/{total})"
                )

        # Session history and trends
        if len(self.session_history) > 1:
            logger.info(f"\nSession History (last {min(10, len(self.session_history))} sessions):")
            for session in self.session_history[-10:]:
                total = session.total_validated + session.total_failed
                logger.info(
                    f"  {session.session_id}: {session.success_rate:6.2%} success "
                    f"({session.total_validated}/{total})"
                )

        # Trending heuristics
        improving = self.get_improving_heuristics(min_sessions=3)
        declining = self.get_declining_heuristics(min_sessions=3)

        if improving:
            logger.info(f"\nImproving Heuristics ({len(improving)}):")
            for source in improving:
                logger.info(f"  ✓ {source}")

        if declining:
            logger.info(f"\nDeclining Heuristics ({len(declining)}) - May Need Attention:")
            for source in declining:
                logger.info(f"  ⚠ {source}")

        # Current session summary
        logger.info(f"\nCurrent Session ({self.current_session.session_id}):")
        session_total = self.current_session.total_validated + self.current_session.total_failed
        if session_total > 0:
            logger.info(f"  Validated: {self.current_session.total_validated}")
            logger.info(f"  Failed: {self.current_session.total_failed}")
            logger.info(f"  Success Rate: {self.current_session.success_rate:.2%}")

        logger.info("=" * 60)
