"""
Enhanced metrics collection with stage-specific tracking and Prometheus export support.

Provides detailed metrics for each pipeline stage:
- Stage 1 (Discovery): URLs per domain, per discovery source
- Stage 2 (Validation): Status code distribution, latency percentiles
- Stage 3 (Enrichment): Content type distribution, page size statistics
"""

import time
import json
import statistics
from collections import defaultdict, Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class DiscoverySource(Enum):
    """Source of discovered URL"""
    STATIC_LINK = "static_link"
    DYNAMIC_AJAX = "dynamic_ajax"
    HEADLESS_BROWSER = "headless_browser"
    SITEMAP = "sitemap"
    ROBOTS_TXT = "robots_txt"
    SEED = "seed"


@dataclass
class Stage1Metrics:
    """Discovery stage metrics"""
    stage_name: str = "discovery"
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # Basic counts
    items_processed: int = 0
    items_succeeded: int = 0
    items_failed: int = 0

    # Stage 1 specific
    urls_per_domain: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    urls_per_source: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    urls_per_depth: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    duplicate_urls_filtered: int = 0
    robots_txt_blocked: int = 0
    total_pages_crawled: int = 0

    # Dynamic content discovery
    headless_browser_invocations: int = 0
    ajax_endpoints_discovered: int = 0
    javascript_urls_found: int = 0

    errors: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        """Get stage duration in seconds"""
        if self.end_time is None:
            return time.time() - self.start_time
        return self.end_time - self.start_time

    @property
    def success_rate(self) -> float:
        """Get success rate as percentage"""
        if self.items_processed == 0:
            return 0.0
        return (self.items_succeeded / self.items_processed) * 100

    @property
    def throughput(self) -> float:
        """Get throughput in items per second"""
        duration = self.duration
        if duration == 0:
            return 0.0
        return self.items_processed / duration


@dataclass
class Stage2Metrics:
    """Validation stage metrics"""
    stage_name: str = "validation"
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # Basic counts
    items_processed: int = 0
    items_succeeded: int = 0
    items_failed: int = 0

    # Stage 2 specific
    status_code_distribution: Dict[int, int] = field(default_factory=lambda: defaultdict(int))
    error_type_distribution: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    response_times: List[float] = field(default_factory=list)  # milliseconds
    content_length_bytes: List[int] = field(default_factory=list)

    # Retry and circuit breaker stats
    total_retries: int = 0
    circuit_breaker_opens: int = 0
    circuit_breaker_blocks: int = 0

    # Per-domain stats
    requests_per_domain: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    errors_per_domain: Dict[str, int] = field(default_factory=lambda: defaultdict(int))

    errors: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.end_time is None:
            return time.time() - self.start_time
        return self.end_time - self.start_time

    @property
    def success_rate(self) -> float:
        if self.items_processed == 0:
            return 0.0
        return (self.items_succeeded / self.items_processed) * 100

    @property
    def throughput(self) -> float:
        duration = self.duration
        if duration == 0:
            return 0.0
        return self.items_processed / duration

    @property
    def avg_response_time(self) -> float:
        """Average response time in milliseconds"""
        if not self.response_times:
            return 0.0
        return statistics.mean(self.response_times)

    @property
    def p50_response_time(self) -> float:
        """Median (50th percentile) response time in milliseconds"""
        if not self.response_times:
            return 0.0
        return statistics.median(self.response_times)

    @property
    def p95_response_time(self) -> float:
        """95th percentile response time in milliseconds"""
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[idx] if idx < len(sorted_times) else sorted_times[-1]

    @property
    def p99_response_time(self) -> float:
        """99th percentile response time in milliseconds"""
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[idx] if idx < len(sorted_times) else sorted_times[-1]


@dataclass
class Stage3Metrics:
    """Enrichment stage metrics"""
    stage_name: str = "enrichment"
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None

    # Basic counts
    items_processed: int = 0
    items_succeeded: int = 0
    items_failed: int = 0

    # Stage 3 specific
    content_type_distribution: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    page_word_counts: List[int] = field(default_factory=list)
    page_sizes_bytes: List[int] = field(default_factory=list)

    # Content processing
    html_pages_processed: int = 0
    pdfs_processed: int = 0
    images_processed: int = 0
    videos_processed: int = 0
    other_media_processed: int = 0

    # NLP/Entity extraction
    total_entities_extracted: int = 0
    total_keywords_extracted: int = 0
    nlp_processing_time_ms: List[float] = field(default_factory=list)

    # Headless browser usage
    headless_browser_pages: int = 0
    javascript_execution_count: int = 0

    errors: List[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        if self.end_time is None:
            return time.time() - self.start_time
        return self.end_time - self.start_time

    @property
    def success_rate(self) -> float:
        if self.items_processed == 0:
            return 0.0
        return (self.items_succeeded / self.items_processed) * 100

    @property
    def throughput(self) -> float:
        duration = self.duration
        if duration == 0:
            return 0.0
        return self.items_processed / duration

    @property
    def avg_page_word_count(self) -> float:
        """Average page size in words"""
        if not self.page_word_counts:
            return 0.0
        return statistics.mean(self.page_word_counts)

    @property
    def median_page_word_count(self) -> float:
        """Median page size in words"""
        if not self.page_word_counts:
            return 0.0
        return statistics.median(self.page_word_counts)

    @property
    def avg_page_size_kb(self) -> float:
        """Average page size in kilobytes"""
        if not self.page_sizes_bytes:
            return 0.0
        return statistics.mean(self.page_sizes_bytes) / 1024


class EnhancedMetricsCollector:
    """
    Enhanced metrics collector with stage-specific detailed tracking.
    Thread-safe for concurrent operations.
    """

    def __init__(self):
        self.pipeline_start_time = time.time()
        self.pipeline_end_time: Optional[float] = None

        # Stage-specific metrics
        self.stage1_metrics: Optional[Stage1Metrics] = None
        self.stage2_metrics: Optional[Stage2Metrics] = None
        self.stage3_metrics: Optional[Stage3Metrics] = None

        # Lock for thread safety
        import threading
        self._lock = threading.Lock()

        logger.info("Enhanced metrics collector initialized")

    # ===== Stage 1 (Discovery) Methods =====

    def start_stage1(self) -> Stage1Metrics:
        """Start tracking Stage 1 metrics"""
        with self._lock:
            self.stage1_metrics = Stage1Metrics()
            logger.info("Started Stage 1 metrics tracking")
            return self.stage1_metrics

    def record_discovered_url(self, domain: str, source: str, depth: int = 0):
        """Record a discovered URL"""
        with self._lock:
            if self.stage1_metrics:
                self.stage1_metrics.urls_per_domain[domain] += 1
                self.stage1_metrics.urls_per_source[source] += 1
                self.stage1_metrics.urls_per_depth[depth] += 1
                self.stage1_metrics.items_processed += 1
                self.stage1_metrics.items_succeeded += 1

    def record_duplicate_filtered(self, count: int = 1):
        """Record duplicate URLs filtered"""
        with self._lock:
            if self.stage1_metrics:
                self.stage1_metrics.duplicate_urls_filtered += count

    def record_robots_blocked(self, count: int = 1):
        """Record URLs blocked by robots.txt"""
        with self._lock:
            if self.stage1_metrics:
                self.stage1_metrics.robots_txt_blocked += count

    def record_headless_browser_use(self, ajax_count: int = 0, js_urls: int = 0):
        """Record headless browser usage"""
        with self._lock:
            if self.stage1_metrics:
                self.stage1_metrics.headless_browser_invocations += 1
                self.stage1_metrics.ajax_endpoints_discovered += ajax_count
                self.stage1_metrics.javascript_urls_found += js_urls

    def record_page_crawled(self):
        """Record a page crawled"""
        with self._lock:
            if self.stage1_metrics:
                self.stage1_metrics.total_pages_crawled += 1

    def end_stage1(self):
        """End Stage 1 tracking"""
        with self._lock:
            if self.stage1_metrics:
                self.stage1_metrics.end_time = time.time()
                logger.info("Ended Stage 1 metrics tracking")

    # ===== Stage 2 (Validation) Methods =====

    def start_stage2(self) -> Stage2Metrics:
        """Start tracking Stage 2 metrics"""
        with self._lock:
            self.stage2_metrics = Stage2Metrics()
            logger.info("Started Stage 2 metrics tracking")
            return self.stage2_metrics

    def record_validation_result(
        self,
        domain: str,
        status_code: int,
        response_time_ms: float,
        content_length: int = 0,
        success: bool = True,
        error_type: Optional[str] = None
    ):
        """Record a validation result"""
        with self._lock:
            if self.stage2_metrics:
                self.stage2_metrics.items_processed += 1
                self.stage2_metrics.status_code_distribution[status_code] += 1
                self.stage2_metrics.response_times.append(response_time_ms)
                self.stage2_metrics.requests_per_domain[domain] += 1

                if content_length > 0:
                    self.stage2_metrics.content_length_bytes.append(content_length)

                if success:
                    self.stage2_metrics.items_succeeded += 1
                else:
                    self.stage2_metrics.items_failed += 1
                    self.stage2_metrics.errors_per_domain[domain] += 1
                    if error_type:
                        self.stage2_metrics.error_type_distribution[error_type] += 1

    def record_retry(self):
        """Record a retry attempt"""
        with self._lock:
            if self.stage2_metrics:
                self.stage2_metrics.total_retries += 1

    def record_circuit_breaker_open(self):
        """Record circuit breaker opening"""
        with self._lock:
            if self.stage2_metrics:
                self.stage2_metrics.circuit_breaker_opens += 1

    def record_circuit_breaker_block(self):
        """Record request blocked by circuit breaker"""
        with self._lock:
            if self.stage2_metrics:
                self.stage2_metrics.circuit_breaker_blocks += 1

    def end_stage2(self):
        """End Stage 2 tracking"""
        with self._lock:
            if self.stage2_metrics:
                self.stage2_metrics.end_time = time.time()
                logger.info("Ended Stage 2 metrics tracking")

    # ===== Stage 3 (Enrichment) Methods =====

    def start_stage3(self) -> Stage3Metrics:
        """Start tracking Stage 3 metrics"""
        with self._lock:
            self.stage3_metrics = Stage3Metrics()
            logger.info("Started Stage 3 metrics tracking")
            return self.stage3_metrics

    def record_enrichment_result(
        self,
        content_type: str,
        word_count: int = 0,
        size_bytes: int = 0,
        success: bool = True,
        entities_count: int = 0,
        keywords_count: int = 0,
        nlp_time_ms: float = 0.0
    ):
        """Record an enrichment result"""
        with self._lock:
            if self.stage3_metrics:
                self.stage3_metrics.items_processed += 1
                self.stage3_metrics.content_type_distribution[content_type] += 1

                if word_count > 0:
                    self.stage3_metrics.page_word_counts.append(word_count)
                if size_bytes > 0:
                    self.stage3_metrics.page_sizes_bytes.append(size_bytes)

                # Count by content type
                if "html" in content_type.lower():
                    self.stage3_metrics.html_pages_processed += 1
                elif "pdf" in content_type.lower():
                    self.stage3_metrics.pdfs_processed += 1
                elif "image" in content_type.lower():
                    self.stage3_metrics.images_processed += 1
                elif "video" in content_type.lower():
                    self.stage3_metrics.videos_processed += 1
                else:
                    self.stage3_metrics.other_media_processed += 1

                if success:
                    self.stage3_metrics.items_succeeded += 1
                else:
                    self.stage3_metrics.items_failed += 1

                # NLP stats
                self.stage3_metrics.total_entities_extracted += entities_count
                self.stage3_metrics.total_keywords_extracted += keywords_count
                if nlp_time_ms > 0:
                    self.stage3_metrics.nlp_processing_time_ms.append(nlp_time_ms)

    def record_headless_browser_page(self):
        """Record headless browser usage"""
        with self._lock:
            if self.stage3_metrics:
                self.stage3_metrics.headless_browser_pages += 1

    def record_javascript_execution(self):
        """Record JavaScript execution"""
        with self._lock:
            if self.stage3_metrics:
                self.stage3_metrics.javascript_execution_count += 1

    def end_stage3(self):
        """End Stage 3 tracking"""
        with self._lock:
            if self.stage3_metrics:
                self.stage3_metrics.end_time = time.time()
                logger.info("Ended Stage 3 metrics tracking")

    # ===== Summary and Export Methods =====

    def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive metrics summary"""
        with self._lock:
            pipeline_duration = (
                self.pipeline_end_time or time.time()
            ) - self.pipeline_start_time

            summary = {
                "pipeline_duration_seconds": pipeline_duration,
                "timestamp": datetime.now().isoformat(),
            }

            # Stage 1 summary
            if self.stage1_metrics:
                s1 = self.stage1_metrics
                summary["stage1_discovery"] = {
                    "duration_seconds": s1.duration,
                    "items_processed": s1.items_processed,
                    "success_rate_percent": s1.success_rate,
                    "throughput_items_per_sec": s1.throughput,
                    "urls_per_domain": dict(s1.urls_per_domain),
                    "urls_per_source": dict(s1.urls_per_source),
                    "urls_per_depth": {str(k): v for k, v in s1.urls_per_depth.items()},
                    "duplicates_filtered": s1.duplicate_urls_filtered,
                    "robots_blocked": s1.robots_txt_blocked,
                    "pages_crawled": s1.total_pages_crawled,
                    "headless_browser_invocations": s1.headless_browser_invocations,
                    "ajax_endpoints_discovered": s1.ajax_endpoints_discovered,
                    "javascript_urls_found": s1.javascript_urls_found,
                }

            # Stage 2 summary
            if self.stage2_metrics:
                s2 = self.stage2_metrics
                summary["stage2_validation"] = {
                    "duration_seconds": s2.duration,
                    "items_processed": s2.items_processed,
                    "success_rate_percent": s2.success_rate,
                    "throughput_items_per_sec": s2.throughput,
                    "status_code_distribution": dict(s2.status_code_distribution),
                    "error_type_distribution": dict(s2.error_type_distribution),
                    "response_time_ms": {
                        "avg": s2.avg_response_time,
                        "p50": s2.p50_response_time,
                        "p95": s2.p95_response_time,
                        "p99": s2.p99_response_time,
                    },
                    "total_retries": s2.total_retries,
                    "circuit_breaker_opens": s2.circuit_breaker_opens,
                    "circuit_breaker_blocks": s2.circuit_breaker_blocks,
                    "requests_per_domain": dict(s2.requests_per_domain),
                    "errors_per_domain": dict(s2.errors_per_domain),
                }

            # Stage 3 summary
            if self.stage3_metrics:
                s3 = self.stage3_metrics
                summary["stage3_enrichment"] = {
                    "duration_seconds": s3.duration,
                    "items_processed": s3.items_processed,
                    "success_rate_percent": s3.success_rate,
                    "throughput_items_per_sec": s3.throughput,
                    "content_type_distribution": dict(s3.content_type_distribution),
                    "page_statistics": {
                        "avg_word_count": s3.avg_page_word_count,
                        "median_word_count": s3.median_page_word_count,
                        "avg_size_kb": s3.avg_page_size_kb,
                    },
                    "content_processed": {
                        "html_pages": s3.html_pages_processed,
                        "pdfs": s3.pdfs_processed,
                        "images": s3.images_processed,
                        "videos": s3.videos_processed,
                        "other": s3.other_media_processed,
                    },
                    "nlp_statistics": {
                        "total_entities_extracted": s3.total_entities_extracted,
                        "total_keywords_extracted": s3.total_keywords_extracted,
                        "avg_nlp_time_ms": (
                            statistics.mean(s3.nlp_processing_time_ms)
                            if s3.nlp_processing_time_ms else 0.0
                        ),
                    },
                    "headless_browser_pages": s3.headless_browser_pages,
                    "javascript_executions": s3.javascript_execution_count,
                }

            return summary

    def log_summary(self):
        """Log detailed metrics summary"""
        summary = self.get_summary()

        logger.info("=" * 80)
        logger.info("ENHANCED PIPELINE METRICS SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Pipeline Duration: {summary['pipeline_duration_seconds']:.2f}s")

        # Stage 1
        if "stage1_discovery" in summary:
            s1 = summary["stage1_discovery"]
            logger.info("\nSTAGE 1 - DISCOVERY:")
            logger.info(f"  Duration: {s1['duration_seconds']:.2f}s")
            logger.info(f"  Items: {s1['items_processed']:,}")
            logger.info(f"  Success Rate: {s1['success_rate_percent']:.1f}%")
            logger.info(f"  Throughput: {s1['throughput_items_per_sec']:.1f} items/s")
            logger.info(f"  Pages Crawled: {s1['pages_crawled']:,}")
            logger.info(f"  Duplicates Filtered: {s1['duplicates_filtered']:,}")

            logger.info("  URLs by Domain:")
            for domain, count in sorted(s1['urls_per_domain'].items(), key=lambda x: x[1], reverse=True)[:10]:
                logger.info(f"    {domain}: {count:,}")

            logger.info("  URLs by Source:")
            for source, count in s1['urls_per_source'].items():
                logger.info(f"    {source}: {count:,}")

            if s1['headless_browser_invocations'] > 0:
                logger.info(f"  Headless Browser: {s1['headless_browser_invocations']:,} invocations")
                logger.info(f"    AJAX Endpoints: {s1['ajax_endpoints_discovered']:,}")
                logger.info(f"    JS URLs: {s1['javascript_urls_found']:,}")

        # Stage 2
        if "stage2_validation" in summary:
            s2 = summary["stage2_validation"]
            logger.info("\nSTAGE 2 - VALIDATION:")
            logger.info(f"  Duration: {s2['duration_seconds']:.2f}s")
            logger.info(f"  Items: {s2['items_processed']:,}")
            logger.info(f"  Success Rate: {s2['success_rate_percent']:.1f}%")
            logger.info(f"  Throughput: {s2['throughput_items_per_sec']:.1f} items/s")

            logger.info("  Response Times (ms):")
            logger.info(f"    Avg: {s2['response_time_ms']['avg']:.1f}")
            logger.info(f"    P50: {s2['response_time_ms']['p50']:.1f}")
            logger.info(f"    P95: {s2['response_time_ms']['p95']:.1f}")
            logger.info(f"    P99: {s2['response_time_ms']['p99']:.1f}")

            logger.info("  Status Code Distribution:")
            for code, count in sorted(s2['status_code_distribution'].items()):
                logger.info(f"    {code}: {count:,}")

            if s2['total_retries'] > 0:
                logger.info(f"  Retries: {s2['total_retries']:,}")
            if s2['circuit_breaker_opens'] > 0:
                logger.info(f"  Circuit Breaker Opens: {s2['circuit_breaker_opens']}")
                logger.info(f"  Circuit Breaker Blocks: {s2['circuit_breaker_blocks']}")

        # Stage 3
        if "stage3_enrichment" in summary:
            s3 = summary["stage3_enrichment"]
            logger.info("\nSTAGE 3 - ENRICHMENT:")
            logger.info(f"  Duration: {s3['duration_seconds']:.2f}s")
            logger.info(f"  Items: {s3['items_processed']:,}")
            logger.info(f"  Success Rate: {s3['success_rate_percent']:.1f}%")
            logger.info(f"  Throughput: {s3['throughput_items_per_sec']:.1f} items/s")

            logger.info("  Content Processed:")
            for ctype, count in s3['content_processed'].items():
                logger.info(f"    {ctype}: {count:,}")

            logger.info("  Page Statistics:")
            logger.info(f"    Avg Word Count: {s3['page_statistics']['avg_word_count']:.0f}")
            logger.info(f"    Median Word Count: {s3['page_statistics']['median_word_count']:.0f}")
            logger.info(f"    Avg Size: {s3['page_statistics']['avg_size_kb']:.1f} KB")

            logger.info("  NLP Statistics:")
            logger.info(f"    Entities Extracted: {s3['nlp_statistics']['total_entities_extracted']:,}")
            logger.info(f"    Keywords Extracted: {s3['nlp_statistics']['total_keywords_extracted']:,}")
            logger.info(f"    Avg NLP Time: {s3['nlp_statistics']['avg_nlp_time_ms']:.1f}ms")

        logger.info("=" * 80)

    def export_to_file(self, output_path: Path):
        """Export metrics to JSON file"""
        summary = self.get_summary()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w') as f:
            json.dump(summary, f, indent=2)

        logger.info(f"Metrics exported to {output_path}")

    def end_pipeline(self):
        """Mark pipeline as complete"""
        self.pipeline_end_time = time.time()


# Global instance
_enhanced_metrics_collector: Optional[EnhancedMetricsCollector] = None


def get_enhanced_metrics_collector() -> EnhancedMetricsCollector:
    """Get the global enhanced metrics collector instance"""
    global _enhanced_metrics_collector
    if _enhanced_metrics_collector is None:
        _enhanced_metrics_collector = EnhancedMetricsCollector()
    return _enhanced_metrics_collector


def reset_enhanced_metrics():
    """Reset the global enhanced metrics collector"""
    global _enhanced_metrics_collector
    _enhanced_metrics_collector = EnhancedMetricsCollector()
