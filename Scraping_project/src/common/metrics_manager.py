"""Lightweight, named convenience wrapper around the pipeline's UDP
StatsD metrics.

monitoring/metrics_exporter.py's StatsDClient/MetricsExporter poll
Delta Lake tables on an interval to derive metrics; this module is for
call sites inside the pipeline itself (spiders, workers) that want to
fire a specific named event as it happens, without needing to know the
underlying StatsD metric name or tag shape.

Deliberately does not import from monitoring/ - that package depends
on src/, not the other way around - so this carries its own minimal
UDP sender rather than reusing StatsDClient directly.
"""

import logging
import os
import socket

from src.utils.metric_labels import identity_labels

logger = logging.getLogger(__name__)


class MetricsManager:
    """Fire-and-forget UDP StatsD counters for named pipeline events."""

    def __init__(self, host: str | None = None, port: int | None = None):
        self.host = host or os.environ.get("STATSD_HOST", "localhost")
        self.port = port or int(os.environ.get("STATSD_PORT", "8125"))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _increment(self, metric: str, amount: int = 1, tags: dict[str, str] | None = None) -> None:
        tag_str = ""
        if tags:
            tag_str = "|#" + ",".join(f"{key}:{value}" for key, value in tags.items())
        try:
            self._sock.sendto(f"{metric}:{amount}|c{tag_str}".encode("utf-8"), (self.host, self.port))
        except Exception as e:
            logger.debug(f"Failed to send metric {metric}: {e}")

    def record_stage1_url_discovered(self, count: int = 1) -> None:
        # Same label set as metrics_exporter's urls.discovered.total (#243).
        self._increment("urls.discovered.total", count, identity_labels(stage="stage1"))

    def record_stage2_page_analyzed(self, count: int = 1) -> None:
        # stage is required: the exporter emits this family with stage, and a
        # series that omits it breaks sum by (stage). site_id stays configured.
        self._increment("urls.processed.total", count, identity_labels(stage="stage2"))

    def record_stage3_summary_created(self, count: int = 1) -> None:
        self._increment("summaries.created.total", count, identity_labels(stage="stage3"))

    def close(self) -> None:
        self._sock.close()
