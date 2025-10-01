"""
Prometheus metrics exporter for pipeline monitoring.

Supports multiple export modes:
1. Text file export (Prometheus text format for node_exporter textfile collector)
2. HTTP server (scrape endpoint)
3. Pushgateway push (for batch jobs)
"""

import time
from pathlib import Path
from typing import Dict, Optional, Any
from datetime import datetime
import logging

try:
    from prometheus_client import (
        CollectorRegistry,
        Gauge,
        Counter,
        Histogram,
        Summary,
        generate_latest,
        push_to_gateway,
        start_http_server,
        write_to_textfile
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

from .enhanced_metrics import EnhancedMetricsCollector

logger = logging.getLogger(__name__)


class PrometheusExporter:
    """
    Prometheus metrics exporter with multiple output modes.

    Export Modes:
    - textfile: Write to file for node_exporter textfile collector
    - http: Start HTTP server for Prometheus scraping
    - pushgateway: Push to Prometheus Pushgateway
    """

    def __init__(
        self,
        job_name: str = "scraping_pipeline",
        namespace: str = "pipeline",
        enable_http_server: bool = False,
        http_port: int = 8000,
        pushgateway_url: Optional[str] = None
    ):
        """
        Initialize Prometheus exporter.

        Args:
            job_name: Job name for metrics labels
            namespace: Metrics namespace prefix
            enable_http_server: Start HTTP server for scraping
            http_port: HTTP server port
            pushgateway_url: Pushgateway URL (e.g., 'localhost:9091')
        """
        if not PROMETHEUS_AVAILABLE:
            logger.warning(
                "prometheus_client not installed. "
                "Install with: pip install prometheus-client"
            )
            self.enabled = False
            return

        self.enabled = True
        self.job_name = job_name
        self.namespace = namespace
        self.pushgateway_url = pushgateway_url

        # Create registry
        self.registry = CollectorRegistry()

        # Initialize metrics
        self._init_metrics()

        # Start HTTP server if enabled
        if enable_http_server:
            try:
                start_http_server(http_port, registry=self.registry)
                logger.info(f"Prometheus HTTP server started on port {http_port}")
            except Exception as e:
                logger.error(f"Failed to start Prometheus HTTP server: {e}")

    def _init_metrics(self):
        """Initialize Prometheus metrics"""

        # Pipeline-level metrics
        self.pipeline_duration = Gauge(
            f"{self.namespace}_pipeline_duration_seconds",
            "Total pipeline duration in seconds",
            registry=self.registry
        )

        # Stage 1 - Discovery
        self.stage1_duration = Gauge(
            f"{self.namespace}_stage1_duration_seconds",
            "Stage 1 duration",
            registry=self.registry
        )
        self.stage1_items_processed = Counter(
            f"{self.namespace}_stage1_items_total",
            "Total items processed in Stage 1",
            registry=self.registry
        )
        self.stage1_success_rate = Gauge(
            f"{self.namespace}_stage1_success_rate",
            "Stage 1 success rate (0-100)",
            registry=self.registry
        )
        self.stage1_urls_by_domain = Gauge(
            f"{self.namespace}_stage1_urls_by_domain",
            "URLs discovered per domain",
            ["domain"],
            registry=self.registry
        )
        self.stage1_urls_by_source = Gauge(
            f"{self.namespace}_stage1_urls_by_source",
            "URLs discovered per source",
            ["source"],
            registry=self.registry
        )
        self.stage1_duplicates_filtered = Counter(
            f"{self.namespace}_stage1_duplicates_filtered_total",
            "Duplicate URLs filtered",
            registry=self.registry
        )
        self.stage1_headless_invocations = Counter(
            f"{self.namespace}_stage1_headless_browser_total",
            "Headless browser invocations",
            registry=self.registry
        )

        # Stage 2 - Validation
        self.stage2_duration = Gauge(
            f"{self.namespace}_stage2_duration_seconds",
            "Stage 2 duration",
            registry=self.registry
        )
        self.stage2_items_processed = Counter(
            f"{self.namespace}_stage2_items_total",
            "Total items processed in Stage 2",
            registry=self.registry
        )
        self.stage2_success_rate = Gauge(
            f"{self.namespace}_stage2_success_rate",
            "Stage 2 success rate (0-100)",
            registry=self.registry
        )
        self.stage2_status_codes = Gauge(
            f"{self.namespace}_stage2_status_codes",
            "HTTP status code distribution",
            ["status_code"],
            registry=self.registry
        )
        self.stage2_response_time_summary = Summary(
            f"{self.namespace}_stage2_response_time_ms",
            "Response time in milliseconds",
            registry=self.registry
        )
        self.stage2_response_time_histogram = Histogram(
            f"{self.namespace}_stage2_response_time_ms_histogram",
            "Response time distribution",
            buckets=[10, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
            registry=self.registry
        )
        self.stage2_retries = Counter(
            f"{self.namespace}_stage2_retries_total",
            "Total retry attempts",
            registry=self.registry
        )
        self.stage2_circuit_breaker_opens = Counter(
            f"{self.namespace}_stage2_circuit_breaker_opens_total",
            "Circuit breaker opens",
            registry=self.registry
        )
        self.stage2_requests_by_domain = Gauge(
            f"{self.namespace}_stage2_requests_by_domain",
            "Requests per domain",
            ["domain"],
            registry=self.registry
        )

        # Stage 3 - Enrichment
        self.stage3_duration = Gauge(
            f"{self.namespace}_stage3_duration_seconds",
            "Stage 3 duration",
            registry=self.registry
        )
        self.stage3_items_processed = Counter(
            f"{self.namespace}_stage3_items_total",
            "Total items processed in Stage 3",
            registry=self.registry
        )
        self.stage3_success_rate = Gauge(
            f"{self.namespace}_stage3_success_rate",
            "Stage 3 success rate (0-100)",
            registry=self.registry
        )
        self.stage3_content_by_type = Gauge(
            f"{self.namespace}_stage3_content_by_type",
            "Content processed by type",
            ["content_type"],
            registry=self.registry
        )
        self.stage3_avg_page_words = Gauge(
            f"{self.namespace}_stage3_avg_page_words",
            "Average page word count",
            registry=self.registry
        )
        self.stage3_avg_page_size_kb = Gauge(
            f"{self.namespace}_stage3_avg_page_size_kb",
            "Average page size in KB",
            registry=self.registry
        )
        self.stage3_entities_extracted = Counter(
            f"{self.namespace}_stage3_entities_extracted_total",
            "Total entities extracted",
            registry=self.registry
        )
        self.stage3_keywords_extracted = Counter(
            f"{self.namespace}_stage3_keywords_extracted_total",
            "Total keywords extracted",
            registry=self.registry
        )
        self.stage3_nlp_time_summary = Summary(
            f"{self.namespace}_stage3_nlp_time_ms",
            "NLP processing time in milliseconds",
            registry=self.registry
        )

    def update_from_collector(self, collector: EnhancedMetricsCollector):
        """
        Update Prometheus metrics from EnhancedMetricsCollector.

        Args:
            collector: Enhanced metrics collector instance
        """
        if not self.enabled:
            return

        summary = collector.get_summary()

        # Pipeline duration
        self.pipeline_duration.set(summary.get("pipeline_duration_seconds", 0))

        # Stage 1
        if "stage1_discovery" in summary:
            s1 = summary["stage1_discovery"]
            self.stage1_duration.set(s1["duration_seconds"])

            # Use _value for Counter since we're setting total
            items = s1["items_processed"]
            if items > 0:
                # Reset and set to current value
                self.stage1_items_processed._value.set(items)

            self.stage1_success_rate.set(s1["success_rate_percent"])

            # URLs by domain
            for domain, count in s1["urls_per_domain"].items():
                self.stage1_urls_by_domain.labels(domain=domain).set(count)

            # URLs by source
            for source, count in s1["urls_per_source"].items():
                self.stage1_urls_by_source.labels(source=source).set(count)

            # Duplicates
            if s1["duplicates_filtered"] > 0:
                self.stage1_duplicates_filtered._value.set(s1["duplicates_filtered"])

            # Headless browser
            if s1["headless_browser_invocations"] > 0:
                self.stage1_headless_invocations._value.set(s1["headless_browser_invocations"])

        # Stage 2
        if "stage2_validation" in summary:
            s2 = summary["stage2_validation"]
            self.stage2_duration.set(s2["duration_seconds"])

            items = s2["items_processed"]
            if items > 0:
                self.stage2_items_processed._value.set(items)

            self.stage2_success_rate.set(s2["success_rate_percent"])

            # Status codes
            for code, count in s2["status_code_distribution"].items():
                self.stage2_status_codes.labels(status_code=str(code)).set(count)

            # Response times - populate histogram from raw data
            # Note: In production, you'd observe individual timings
            # Here we're updating from aggregated data

            # Retries
            if s2["total_retries"] > 0:
                self.stage2_retries._value.set(s2["total_retries"])

            # Circuit breaker
            if s2["circuit_breaker_opens"] > 0:
                self.stage2_circuit_breaker_opens._value.set(s2["circuit_breaker_opens"])

            # Requests by domain
            for domain, count in s2["requests_per_domain"].items():
                self.stage2_requests_by_domain.labels(domain=domain).set(count)

        # Stage 3
        if "stage3_enrichment" in summary:
            s3 = summary["stage3_enrichment"]
            self.stage3_duration.set(s3["duration_seconds"])

            items = s3["items_processed"]
            if items > 0:
                self.stage3_items_processed._value.set(items)

            self.stage3_success_rate.set(s3["success_rate_percent"])

            # Content by type
            for content_type, count in s3["content_type_distribution"].items():
                # Sanitize label
                safe_type = content_type.replace("/", "_").replace(".", "_")
                self.stage3_content_by_type.labels(content_type=safe_type).set(count)

            # Page statistics
            self.stage3_avg_page_words.set(s3["page_statistics"]["avg_word_count"])
            self.stage3_avg_page_size_kb.set(s3["page_statistics"]["avg_size_kb"])

            # NLP statistics
            if s3["nlp_statistics"]["total_entities_extracted"] > 0:
                self.stage3_entities_extracted._value.set(
                    s3["nlp_statistics"]["total_entities_extracted"]
                )
            if s3["nlp_statistics"]["total_keywords_extracted"] > 0:
                self.stage3_keywords_extracted._value.set(
                    s3["nlp_statistics"]["total_keywords_extracted"]
                )

    def export_to_textfile(self, output_path: Path):
        """
        Export metrics to textfile for node_exporter textfile collector.

        Args:
            output_path: Path to write metrics file (e.g., /var/lib/node_exporter/scraping_pipeline.prom)
        """
        if not self.enabled:
            logger.warning("Prometheus exporter not enabled")
            return

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            write_to_textfile(str(output_path), self.registry)
            logger.info(f"Prometheus metrics exported to {output_path}")
        except Exception as e:
            logger.error(f"Failed to export metrics to textfile: {e}")

    def push_to_gateway(self, pushgateway_url: Optional[str] = None):
        """
        Push metrics to Prometheus Pushgateway.

        Args:
            pushgateway_url: Pushgateway URL (default: use constructor value)
        """
        if not self.enabled:
            logger.warning("Prometheus exporter not enabled")
            return

        url = pushgateway_url or self.pushgateway_url
        if not url:
            logger.error("No pushgateway URL configured")
            return

        try:
            push_to_gateway(url, job=self.job_name, registry=self.registry)
            logger.info(f"Metrics pushed to Pushgateway at {url}")
        except Exception as e:
            logger.error(f"Failed to push metrics to Pushgateway: {e}")

    def get_text_metrics(self) -> str:
        """
        Get metrics in Prometheus text format.

        Returns:
            Prometheus text format metrics
        """
        if not self.enabled:
            return ""

        return generate_latest(self.registry).decode('utf-8')


def create_prometheus_exporter(
    config: Dict[str, Any],
    job_name: str = "scraping_pipeline"
) -> Optional[PrometheusExporter]:
    """
    Create Prometheus exporter from configuration.

    Args:
        config: Metrics configuration dictionary
        job_name: Job name for metrics

    Returns:
        PrometheusExporter instance or None if disabled
    """
    if not config.get("prometheus_enabled", False):
        logger.info("Prometheus export disabled in configuration")
        return None

    exporter = PrometheusExporter(
        job_name=job_name,
        namespace=config.get("prometheus_namespace", "pipeline"),
        enable_http_server=config.get("prometheus_http_server", False),
        http_port=config.get("prometheus_http_port", 8000),
        pushgateway_url=config.get("prometheus_pushgateway_url")
    )

    if not exporter.enabled:
        return None

    return exporter
