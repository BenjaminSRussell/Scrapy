"""
Monitoring and alerting hooks for the UConn scraping pipeline.

This module provides hooks that can be integrated into the pipeline to send
metrics, logs, and alerts to various monitoring systems.

Supported integrations:
- Prometheus (metrics)
- Slack (alerts)
- Datadog (metrics and logs)
- PagerDuty (incident management)
"""

import logging
import time
from datetime import datetime
from typing import Any

import requests

logger = logging.getLogger(__name__)


class MonitoringHook:
    """Base class for monitoring hooks."""

    def on_pipeline_start(self, context: dict[str, Any]) -> None:
        """Called when pipeline starts."""
        pass

    def on_pipeline_complete(self, context: dict[str, Any]) -> None:
        """Called when pipeline completes successfully."""
        pass

    def on_pipeline_failure(self, context: dict[str, Any], error: Exception) -> None:
        """Called when pipeline fails."""
        pass

    def on_stage_complete(self, stage_name: str, metrics: dict[str, Any]) -> None:
        """Called when a pipeline stage completes."""
        pass


class SlackMonitoringHook(MonitoringHook):
    """Send alerts and status updates to Slack."""

    def __init__(self, webhook_url: str, channel: str = "#data-pipeline"):
        self.webhook_url = webhook_url
        self.channel = channel

    def _send_message(self, text: str, blocks: list[dict] | None = None) -> None:
        """Send a message to Slack."""
        payload = {
            "channel": self.channel,
            "text": text,
        }

        if blocks:
            payload["blocks"] = blocks

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to send Slack message: {e}")

    def on_pipeline_start(self, context: dict[str, Any]) -> None:
        """Notify Slack that pipeline started."""
        self._send_message(
            text="🚀 UConn scraping pipeline started",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Pipeline Started*\n"
                        f"⏰ Time: {datetime.now().isoformat()}\n"
                        f"📋 Run ID: {context.get('run_id', 'N/A')}",
                    },
                },
            ],
        )

    def on_pipeline_complete(self, context: dict[str, Any]) -> None:
        """Notify Slack that pipeline completed successfully."""
        duration = context.get("duration_seconds", 0)
        pages_scraped = context.get("pages_scraped", 0)
        pages_loaded = context.get("pages_loaded", 0)

        self._send_message(
            text="✅ UConn scraping pipeline completed successfully",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Pipeline Completed*\n"
                        f"✅ Status: Success\n"
                        f"⏱️ Duration: {duration:.1f}s\n"
                        f"📄 Pages scraped: {pages_scraped}\n"
                        f"💾 Pages loaded: {pages_loaded}",
                    },
                },
            ],
        )

    def on_pipeline_failure(self, context: dict[str, Any], error: Exception) -> None:
        """Notify Slack that pipeline failed."""
        self._send_message(
            text="❌ UConn scraping pipeline FAILED",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Pipeline Failed*\n"
                        f"❌ Status: Failed\n"
                        f"📋 Run ID: {context.get('run_id', 'N/A')}\n"
                        f"🐛 Error: `{str(error)}`",
                    },
                },
            ],
        )

    def on_stage_complete(self, stage_name: str, metrics: dict[str, Any]) -> None:
        """Notify Slack that a stage completed."""
        duration = metrics.get("duration_seconds", 0)
        records = metrics.get("records_processed", 0)

        self._send_message(
            text=f"Stage {stage_name} completed",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Stage: {stage_name}*\n"
                        f"⏱️ Duration: {duration:.1f}s\n"
                        f"📊 Records: {records}",
                    },
                },
            ],
        )


class PrometheusMonitoringHook(MonitoringHook):
    """Push metrics to Prometheus Pushgateway."""

    def __init__(self, pushgateway_url: str, job_name: str = "uconn_scraper"):
        self.pushgateway_url = pushgateway_url
        self.job_name = job_name

    def _push_metrics(self, metrics: dict[str, float]) -> None:
        """Push metrics to Prometheus Pushgateway."""
        # Convert metrics to Prometheus format
        lines = []
        for name, value in metrics.items():
            metric_name = f"uconn_scraper_{name}"
            lines.append(f"{metric_name} {value}")

        payload = "\n".join(lines)

        try:
            url = f"{self.pushgateway_url}/metrics/job/{self.job_name}"
            response = requests.post(
                url,
                data=payload,
                headers={"Content-Type": "text/plain"},
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to push metrics to Prometheus: {e}")

    def on_pipeline_complete(self, context: dict[str, Any]) -> None:
        """Push pipeline metrics to Prometheus."""
        self._push_metrics(
            {
                "pipeline_duration_seconds": context.get("duration_seconds", 0),
                "pages_scraped_total": context.get("pages_scraped", 0),
                "pages_loaded_total": context.get("pages_loaded", 0),
                "pipeline_success": 1,
            }
        )

    def on_pipeline_failure(self, context: dict[str, Any], error: Exception) -> None:
        """Push failure metric to Prometheus."""
        self._push_metrics(
            {
                "pipeline_success": 0,
                "pipeline_failures_total": 1,
            }
        )

    def on_stage_complete(self, stage_name: str, metrics: dict[str, Any]) -> None:
        """Push stage metrics to Prometheus."""
        stage_metrics = {
            f"stage_{stage_name}_duration_seconds": metrics.get("duration_seconds", 0),
            f"stage_{stage_name}_records_total": metrics.get("records_processed", 0),
        }
        self._push_metrics(stage_metrics)


class DatadogMonitoringHook(MonitoringHook):
    """Send metrics and events to Datadog."""

    def __init__(self, api_key: str, app_key: str):
        self.api_key = api_key
        self.app_key = app_key
        self.base_url = "https://api.datadoghq.com/api/v1"

    def _send_metric(self, metric_name: str, value: float, tags: list[str] | None = None) -> None:
        """Send a metric to Datadog."""
        now = int(time.time())
        payload = {
            "series": [
                {
                    "metric": f"uconn.scraper.{metric_name}",
                    "points": [[now, value]],
                    "type": "gauge",
                    "tags": tags or [],
                }
            ]
        }

        try:
            response = requests.post(
                f"{self.base_url}/series",
                json=payload,
                headers={"DD-API-KEY": self.api_key},
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to send metric to Datadog: {e}")

    def _send_event(self, title: str, text: str, alert_type: str = "info") -> None:
        """Send an event to Datadog."""
        payload = {
            "title": title,
            "text": text,
            "alert_type": alert_type,  # info, warning, error, success
            "tags": ["service:uconn-scraper"],
        }

        try:
            response = requests.post(
                f"{self.base_url}/events",
                json=payload,
                headers={"DD-API-KEY": self.api_key},
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to send event to Datadog: {e}")

    def on_pipeline_start(self, context: dict[str, Any]) -> None:
        """Send pipeline start event to Datadog."""
        self._send_event(
            title="UConn Scraper: Pipeline Started",
            text=f"Pipeline run {context.get('run_id', 'N/A')} started",
            alert_type="info",
        )

    def on_pipeline_complete(self, context: dict[str, Any]) -> None:
        """Send pipeline completion metrics and event to Datadog."""
        # Send metrics
        self._send_metric("pipeline.duration", context.get("duration_seconds", 0))
        self._send_metric("pages.scraped", context.get("pages_scraped", 0))
        self._send_metric("pages.loaded", context.get("pages_loaded", 0))

        # Send event
        self._send_event(
            title="UConn Scraper: Pipeline Completed",
            text=f"Pipeline completed successfully. "
            f"Scraped {context.get('pages_scraped', 0)} pages, "
            f"loaded {context.get('pages_loaded', 0)} to warehouse.",
            alert_type="success",
        )

    def on_pipeline_failure(self, context: dict[str, Any], error: Exception) -> None:
        """Send pipeline failure event to Datadog."""
        self._send_event(
            title="UConn Scraper: Pipeline Failed",
            text=f"Pipeline failed with error: {str(error)}",
            alert_type="error",
        )


class PagerDutyMonitoringHook(MonitoringHook):
    """Send critical alerts to PagerDuty for on-call engineers."""

    def __init__(self, integration_key: str):
        self.integration_key = integration_key
        self.events_url = "https://events.pagerduty.com/v2/enqueue"

    def _trigger_incident(self, summary: str, severity: str = "error", details: dict | None = None) -> None:
        """Trigger a PagerDuty incident."""
        payload = {
            "routing_key": self.integration_key,
            "event_action": "trigger",
            "payload": {
                "summary": summary,
                "severity": severity,
                "source": "uconn-scraper",
                "custom_details": details or {},
            },
        }

        try:
            response = requests.post(self.events_url, json=payload, timeout=10)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Failed to trigger PagerDuty incident: {e}")

    def on_pipeline_failure(self, context: dict[str, Any], error: Exception) -> None:
        """Trigger PagerDuty incident on pipeline failure."""
        self._trigger_incident(
            summary=f"UConn Scraper Pipeline Failed: {str(error)}",
            severity="error",
            details={
                "run_id": context.get("run_id", "N/A"),
                "error_message": str(error),
                "error_type": type(error).__name__,
                "timestamp": datetime.now().isoformat(),
            },
        )


class MonitoringManager:
    """Manages multiple monitoring hooks."""

    def __init__(self):
        self.hooks: list[MonitoringHook] = []

    def add_hook(self, hook: MonitoringHook) -> None:
        """Add a monitoring hook."""
        self.hooks.append(hook)

    def on_pipeline_start(self, context: dict[str, Any]) -> None:
        """Trigger all hooks for pipeline start."""
        for hook in self.hooks:
            try:
                hook.on_pipeline_start(context)
            except Exception as e:
                logger.error(f"Hook {type(hook).__name__} failed on_pipeline_start: {e}")

    def on_pipeline_complete(self, context: dict[str, Any]) -> None:
        """Trigger all hooks for pipeline completion."""
        for hook in self.hooks:
            try:
                hook.on_pipeline_complete(context)
            except Exception as e:
                logger.error(f"Hook {type(hook).__name__} failed on_pipeline_complete: {e}")

    def on_pipeline_failure(self, context: dict[str, Any], error: Exception) -> None:
        """Trigger all hooks for pipeline failure."""
        for hook in self.hooks:
            try:
                hook.on_pipeline_failure(context, error)
            except Exception as e:
                logger.error(f"Hook {type(hook).__name__} failed on_pipeline_failure: {e}")

    def on_stage_complete(self, stage_name: str, metrics: dict[str, Any]) -> None:
        """Trigger all hooks for stage completion."""
        for hook in self.hooks:
            try:
                hook.on_stage_complete(stage_name, metrics)
            except Exception as e:
                logger.error(f"Hook {type(hook).__name__} failed on_stage_complete: {e}")


# Example usage configuration
def create_monitoring_manager_from_config(config: dict[str, Any]) -> MonitoringManager:
    """Create a MonitoringManager from configuration."""
    manager = MonitoringManager()

    # Slack
    if config.get("slack", {}).get("enabled"):
        slack_hook = SlackMonitoringHook(
            webhook_url=config["slack"]["webhook_url"],
            channel=config["slack"].get("channel", "#data-pipeline"),
        )
        manager.add_hook(slack_hook)

    # Prometheus
    if config.get("prometheus", {}).get("enabled"):
        prometheus_hook = PrometheusMonitoringHook(
            pushgateway_url=config["prometheus"]["pushgateway_url"],
            job_name=config["prometheus"].get("job_name", "uconn_scraper"),
        )
        manager.add_hook(prometheus_hook)

    # Datadog
    if config.get("datadog", {}).get("enabled"):
        datadog_hook = DatadogMonitoringHook(
            api_key=config["datadog"]["api_key"],
            app_key=config["datadog"]["app_key"],
        )
        manager.add_hook(datadog_hook)

    # PagerDuty
    if config.get("pagerduty", {}).get("enabled"):
        pagerduty_hook = PagerDutyMonitoringHook(
            integration_key=config["pagerduty"]["integration_key"],
        )
        manager.add_hook(pagerduty_hook)

    return manager
