"""
Metrics Emitter for UConn Scraping Pipeline

Sends pipeline events to the visualizer metrics server for real-time monitoring.
"""

import logging
from datetime import datetime
from typing import Any

import requests

from src.common.constants import METRICS_ENABLED, METRICS_SERVER_HOST, METRICS_SERVER_PORT

logger = logging.getLogger(__name__)


class MetricsEmitter:
    """
    Emits pipeline events to the metrics server.
    """

    def __init__(self, enabled: bool = METRICS_ENABLED, host: str = METRICS_SERVER_HOST, port: int = METRICS_SERVER_PORT):
        self.enabled = enabled
        self.base_url = f"http://{host}:{port}"
        self.event_endpoint = f"{self.base_url}/event"
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

        if self.enabled:
            logger.info(f"Metrics emitter enabled. Sending to {self.base_url}")
        else:
            logger.info("Metrics emitter disabled.")

    def send_event(self, event_type: str, data: dict[str, Any] | None = None) -> bool:
        """
        Send an event to the metrics server.

        Args:
            event_type: Type of event (url_discovered, url_validated, page_enriched, etc.)
            data: Additional event data

        Returns:
            bool: True if event sent successfully, False otherwise
        """
        if not self.enabled:
            return False

        event = {
            "event": event_type,
            "timestamp": datetime.now().isoformat(),
            **(data or {}),
        }

        try:
            response = self.session.post(
                self.event_endpoint,
                json=event,
                timeout=1.0,  # Don't block pipeline if server is down
            )
            response.raise_for_status()
            return True

        except requests.RequestException as e:
            logger.debug(f"Failed to send event {event_type}: {e}")
            return False

    def url_discovered(self, url: str, source_url: str | None = None, depth: int = 0) -> None:
        """Send url_discovered event."""
        self.send_event(
            "url_discovered",
            {
                "url": url,
                "source_url": source_url,
                "depth": depth,
            },
        )

    def url_validated(self, url: str, status_code: int, success: bool, error: str | None = None) -> None:
        """Send url_validated event."""
        self.send_event(
            "url_validated",
            {
                "url": url,
                "status_code": status_code,
                "success": success,
                "error": error,
            },
        )

    def page_enriched(
        self,
        url: str,
        entities_count: int = 0,
        keywords_count: int = 0,
        categories_count: int = 0,
    ) -> None:
        """Send page_enriched event."""
        self.send_event(
            "page_enriched",
            {
                "url": url,
                "entities_count": entities_count,
                "keywords_count": keywords_count,
                "categories_count": categories_count,
            },
        )

    def pipeline_start(self, pipeline_name: str) -> None:
        """Send pipeline_start event."""
        self.send_event(
            "pipeline_start",
            {
                "pipeline": pipeline_name,
            },
        )

    def pipeline_complete(self, pipeline_name: str, duration_seconds: float, items_processed: int) -> None:
        """Send pipeline_complete event."""
        self.send_event(
            "pipeline_complete",
            {
                "pipeline": pipeline_name,
                "duration_seconds": duration_seconds,
                "items_processed": items_processed,
            },
        )

    def pipeline_error(self, pipeline_name: str, error: str) -> None:
        """Send pipeline_error event."""
        self.send_event(
            "pipeline_error",
            {
                "pipeline": pipeline_name,
                "error": error,
            },
        )


# Global singleton instance
_emitter: MetricsEmitter | None = None


def get_emitter() -> MetricsEmitter:
    """Get the global metrics emitter instance."""
    global _emitter
    if _emitter is None:
        _emitter = MetricsEmitter()
    return _emitter


# Convenience functions
def send_event(event_type: str, data: dict[str, Any] | None = None) -> bool:
    """Send an event using the global emitter."""
    return get_emitter().send_event(event_type, data)


def url_discovered(url: str, source_url: str | None = None, depth: int = 0) -> None:
    """Send url_discovered event."""
    get_emitter().url_discovered(url, source_url, depth)


def url_validated(url: str, status_code: int, success: bool, error: str | None = None) -> None:
    """Send url_validated event."""
    get_emitter().url_validated(url, status_code, success, error)


def page_enriched(url: str, entities_count: int = 0, keywords_count: int = 0, categories_count: int = 0) -> None:
    """Send page_enriched event."""
    get_emitter().page_enriched(url, entities_count, keywords_count, categories_count)


def pipeline_start(pipeline_name: str) -> None:
    """Send pipeline_start event."""
    get_emitter().pipeline_start(pipeline_name)


def pipeline_complete(pipeline_name: str, duration_seconds: float, items_processed: int) -> None:
    """Send pipeline_complete event."""
    get_emitter().pipeline_complete(pipeline_name, duration_seconds, items_processed)


def pipeline_error(pipeline_name: str, error: str) -> None:
    """Send pipeline_error event."""
    get_emitter().pipeline_error(pipeline_name, error)
