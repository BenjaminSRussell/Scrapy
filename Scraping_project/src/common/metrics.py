"""Simple metrics collection for pipeline monitoring."""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class StageMetrics:
    """Metrics for a single pipeline stage."""
    stage_name: str
    start_time: float
    end_time: float | None = None
    items_processed: int = 0
    items_succeeded: int = 0
    items_failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        """Get stage duration in seconds."""
        if self.end_time is None:
            return time.time() - self.start_time
        return self.end_time - self.start_time

    
    @property
    def success_rate(self) -> float:
        """Get success rate as percentage."""
        if self.items_processed == 0:
            return 0.0
        return (self.items_succeeded / self.items_processed) * 100

    @property
    def throughput(self) -> float:
        """Get throughput in items per second."""
        duration = self.duration
        if duration == 0:
            return 0.0
        return self.items_processed / duration


class MetricsCollector:
    """Collects and tracks pipeline metrics."""

    def __init__(self):
        self.stage_metrics: dict[str, StageMetrics] = {}
        self.pipeline_start_time = time.time()

    def start_stage(self, stage_name: str) -> StageMetrics:
        """Start tracking metrics for a stage."""
        metrics = StageMetrics(stage_name=stage_name, start_time=time.time())
        self.stage_metrics[stage_name] = metrics
        logger.info(f"Started tracking metrics for {stage_name}")
        return metrics

    def end_stage(self, stage_name: str):
        """End tracking for a stage."""
        if stage_name in self.stage_metrics:
            self.stage_metrics[stage_name].end_time = time.time()
            logger.info(f"Ended tracking metrics for {stage_name}")

    def record_processed(self, stage_name: str, count: int = 1):
        """Record items processed."""
        if stage_name in self.stage_metrics:
            self.stage_metrics[stage_name].items_processed += count

    def record_success(self, stage_name: str, count: int = 1):
        """Record successful items."""
        if stage_name in self.stage_metrics:
            self.stage_metrics[stage_name].items_succeeded += count

    def record_failure(self, stage_name: str, error: str = "", count: int = 1):
        """Record failed items."""
        if stage_name in self.stage_metrics:
            metrics = self.stage_metrics[stage_name]
            metrics.items_failed += count
            if error:
                metrics.errors.append(error)

    def get_metrics(self, stage_name: str) -> StageMetrics | None:
        """Get metrics for a specific stage."""
        return self.stage_metrics.get(stage_name)

    def get_summary(self) -> dict[str, any]:
        """Get overall pipeline summary."""
        total_items = sum(m.items_processed for m in self.stage_metrics.values())
        total_successes = sum(m.items_succeeded for m in self.stage_metrics.values())
        total_failures = sum(m.items_failed for m in self.stage_metrics.values())
        total_duration = time.time() - self.pipeline_start_time

        return {
            "pipeline_duration": total_duration,
            "total_items_processed": total_items,
            "total_successes": total_successes,
            "total_failures": total_failures,
            "overall_success_rate": (total_successes / max(1, total_items)) * 100,
            "overall_throughput": total_items / max(1, total_duration),
            "stages": {
                name: {
                    "duration": m.duration,
                    "items_processed": m.items_processed,
                    "success_rate": m.success_rate,
                    "throughput": m.throughput,
                    "error_count": m.items_failed
                }
                for name, m in self.stage_metrics.items()
            }
        }

    def log_summary(self):
        """Log a summary of all metrics."""
        summary = self.get_summary()

        logger.info("=" * 60)
        logger.info("PIPELINE METRICS SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total Duration: {summary['pipeline_duration']:.2f}s")
        logger.info(f"Items Processed: {summary['total_items_processed']:,}")
        logger.info(f"Success Rate: {summary['overall_success_rate']:.1f}%")
        logger.info(f"Throughput: {summary['overall_throughput']:.1f} items/s")

        for stage_name, stage_data in summary['stages'].items():
            logger.info(f"\n{stage_name.upper()}:")
            logger.info(f"  Duration: {stage_data['duration']:.2f}s")
            logger.info(f"  Items: {stage_data['items_processed']:,}")
            logger.info(f"  Success Rate: {stage_data['success_rate']:.1f}%")
            logger.info(f"  Throughput: {stage_data['throughput']:.1f} items/s")
            if stage_data['error_count'] > 0:
                logger.info(f"  Errors: {stage_data['error_count']}")


# Global metrics collector instance
_metrics_collector: MetricsCollector | None = None


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector instance."""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector()
    return _metrics_collector


def reset_metrics():
    """Reset the global metrics collector."""
    global _metrics_collector
    _metrics_collector = MetricsCollector()