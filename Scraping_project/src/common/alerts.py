"""Alerting system for pipeline failures and critical events.

Supports multiple notification channels: email, webhook, file-based alerts.
"""

import json
import logging
import smtplib
from dataclasses import asdict, dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class Alert:
    """Represents an alert event."""
    severity: str  # 'info', 'warning', 'error', 'critical'
    title: str
    message: str
    stage: str | None = None
    exception: str | None = None
    timestamp: str = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now().isoformat()


class AlertManager:
    """Manages alerts and notifications for pipeline events."""

    def __init__(self, config: dict[str, Any]):
        """Initialize alert manager with configuration.

        Args:
            config: Alert configuration dictionary with:
                - enabled: bool
                - channels: list of channel configs
                - alert_file: path for file-based alerts
                - severity_threshold: minimum severity to alert
        """
        self.config = config
        self.enabled = config.get('enabled', False)
        self.severity_threshold = config.get('severity_threshold', 'error')
        self.alert_file = Path(config.get('alert_file', 'data/logs/alerts.jsonl'))
        self.channels = self._init_channels(config.get('channels', []))

        # Ensure alert file directory exists
        self.alert_file.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"AlertManager initialized (enabled={self.enabled})")

    def _init_channels(self, channel_configs: list[dict[str, Any]]) -> list['AlertChannel']:
        """Initialize alert channels from config."""
        channels = []
        for channel_config in channel_configs:
            channel_type = channel_config.get('type')
            try:
                if channel_type == 'email':
                    channels.append(EmailChannel(channel_config))
                elif channel_type == 'webhook':
                    channels.append(WebhookChannel(channel_config))
                elif channel_type == 'file':
                    channels.append(FileChannel(channel_config))
                else:
                    logger.warning(f"Unknown alert channel type: {channel_type}")
            except Exception as e:
                logger.error(f"Failed to initialize {channel_type} channel: {e}")
        return channels

    def alert(
        self,
        severity: str,
        title: str,
        message: str,
        stage: str | None = None,
        exception: Exception | None = None,
        **metadata
    ):
        """Send an alert.

        Args:
            severity: Alert severity ('info', 'warning', 'error', 'critical')
            title: Alert title
            message: Alert message
            stage: Pipeline stage (optional)
            exception: Exception object (optional)
            **metadata: Additional metadata
        """
        if not self.enabled:
            logger.debug(f"Alerting disabled, skipping: {title}")
            return

        # Check severity threshold
        if not self._should_alert(severity):
            logger.debug(f"Severity {severity} below threshold, skipping alert")
            return

        # Create alert object
        alert = Alert(
            severity=severity,
            title=title,
            message=message,
            stage=stage,
            exception=str(exception) if exception else None,
            metadata=metadata
        )

        # Log alert
        log_func = getattr(logger, severity, logger.info)
        log_func(f"ALERT: {title} - {message}")

        # Save to alert file
        self._save_alert(alert)

        # Send to channels
        for channel in self.channels:
            try:
                channel.send(alert)
            except Exception as e:
                logger.error(f"Failed to send alert via {channel}: {e}")

    def _should_alert(self, severity: str) -> bool:
        """Check if alert severity meets threshold."""
        severity_levels = {'info': 0, 'warning': 1, 'error': 2, 'critical': 3}
        alert_level = severity_levels.get(severity, 0)
        threshold_level = severity_levels.get(self.severity_threshold, 2)
        return alert_level >= threshold_level

    def _save_alert(self, alert: Alert):
        """Save alert to JSONL file."""
        try:
            with open(self.alert_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(asdict(alert), ensure_ascii=False) + '\n')
        except Exception as e:
            logger.error(f"Failed to save alert to file: {e}")

    # Convenience methods
    def info(self, title: str, message: str, **kwargs):
        """Send info alert."""
        self.alert('info', title, message, **kwargs)

    def warning(self, title: str, message: str, **kwargs):
        """Send warning alert."""
        self.alert('warning', title, message, **kwargs)

    def error(self, title: str, message: str, **kwargs):
        """Send error alert."""
        self.alert('error', title, message, **kwargs)

    def critical(self, title: str, message: str, **kwargs):
        """Send critical alert."""
        self.alert('critical', title, message, **kwargs)

    def stage_failed(self, stage: str, reason: str, exception: Exception | None = None):
        """Alert for stage failure."""
        self.error(
            f"Stage {stage} Failed",
            f"Pipeline stage {stage} failed: {reason}",
            stage=stage,
            exception=exception
        )

    def pipeline_complete(self, stats: dict[str, Any]):
        """Alert for successful pipeline completion."""
        self.info(
            "Pipeline Complete",
            "Pipeline completed successfully",
            **stats
        )


class AlertChannel:
    """Base class for alert channels."""

    def send(self, alert: Alert):
        """Send alert via this channel."""
        raise NotImplementedError


class EmailChannel(AlertChannel):
    """Email alert channel using SMTP."""

    def __init__(self, config: dict[str, Any]):
        """Initialize email channel.

        Args:
            config: Email configuration with:
                - smtp_host: SMTP server host
                - smtp_port: SMTP server port
                - smtp_user: SMTP username
                - smtp_password: SMTP password
                - from_addr: Sender email address
                - to_addrs: List of recipient addresses
                - use_tls: Whether to use TLS
        """
        self.smtp_host = config['smtp_host']
        self.smtp_port = config.get('smtp_port', 587)
        self.smtp_user = config['smtp_user']
        self.smtp_password = config['smtp_password']
        self.from_addr = config['from_addr']
        self.to_addrs = config['to_addrs']
        self.use_tls = config.get('use_tls', True)

    def send(self, alert: Alert):
        """Send alert via email."""
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"[{alert.severity.upper()}] {alert.title}"
        msg['From'] = self.from_addr
        msg['To'] = ', '.join(self.to_addrs)

        # Create text body
        text_body = f"""
Alert: {alert.title}
Severity: {alert.severity}
Stage: {alert.stage or 'N/A'}
Time: {alert.timestamp}

Message:
{alert.message}

{f'Exception: {alert.exception}' if alert.exception else ''}
"""

        msg.attach(MIMEText(text_body, 'plain'))

        # Send email
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            logger.info(f"Email alert sent to {self.to_addrs}")
        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
            raise


class WebhookChannel(AlertChannel):
    """Webhook alert channel for services like Slack, Discord, etc."""

    def __init__(self, config: dict[str, Any]):
        """Initialize webhook channel.

        Args:
            config: Webhook configuration with:
                - url: Webhook URL
                - headers: Optional HTTP headers
                - format: Message format ('slack', 'discord', 'generic')
        """
        self.url = config['url']
        self.headers = config.get('headers', {'Content-Type': 'application/json'})
        self.format = config.get('format', 'generic')
        self.timeout = config.get('timeout', 10)

    def send(self, alert: Alert):
        """Send alert via webhook."""
        payload = self._format_payload(alert)

        try:
            response = requests.post(
                self.url,
                json=payload,
                headers=self.headers,
                timeout=self.timeout
            )
            response.raise_for_status()
            logger.info(f"Webhook alert sent to {self.url}")
        except Exception as e:
            logger.error(f"Failed to send webhook alert: {e}")
            raise

    def _format_payload(self, alert: Alert) -> dict[str, Any]:
        """Format alert for webhook."""
        if self.format == 'slack':
            return self._slack_format(alert)
        elif self.format == 'discord':
            return self._discord_format(alert)
        else:
            return asdict(alert)

    def _slack_format(self, alert: Alert) -> dict[str, Any]:
        """Format for Slack webhook."""
        color_map = {
            'info': '#36a64f',
            'warning': '#ff9800',
            'error': '#f44336',
            'critical': '#9c27b0'
        }

        return {
            "attachments": [{
                "color": color_map.get(alert.severity, '#808080'),
                "title": alert.title,
                "text": alert.message,
                "fields": [
                    {"title": "Severity", "value": alert.severity.upper(), "short": True},
                    {"title": "Stage", "value": alert.stage or 'N/A', "short": True},
                    {"title": "Time", "value": alert.timestamp, "short": False},
                ],
                "footer": "UConn Scraper Pipeline"
            }]
        }

    def _discord_format(self, alert: Alert) -> dict[str, Any]:
        """Format for Discord webhook."""
        color_map = {
            'info': 3447003,  # Blue
            'warning': 16744192,  # Orange
            'error': 15548997,  # Red
            'critical': 10027008  # Purple
        }

        return {
            "embeds": [{
                "title": alert.title,
                "description": alert.message,
                "color": color_map.get(alert.severity, 8421504),
                "fields": [
                    {"name": "Severity", "value": alert.severity.upper(), "inline": True},
                    {"name": "Stage", "value": alert.stage or 'N/A', "inline": True},
                ],
                "footer": {"text": "UConn Scraper Pipeline"},
                "timestamp": alert.timestamp
            }]
        }


class FileChannel(AlertChannel):
    """File-based alert channel (writes to file)."""

    def __init__(self, config: dict[str, Any]):
        """Initialize file channel.

        Args:
            config: File configuration with:
                - path: File path for alerts
                - format: 'json' or 'text'
        """
        self.path = Path(config['path'])
        self.format = config.get('format', 'json')
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def send(self, alert: Alert):
        """Write alert to file."""
        try:
            with open(self.path, 'a', encoding='utf-8') as f:
                if self.format == 'json':
                    f.write(json.dumps(asdict(alert), ensure_ascii=False) + '\n')
                else:
                    f.write(f"[{alert.timestamp}] [{alert.severity.upper()}] "
                           f"{alert.title}: {alert.message}\n")
            logger.debug(f"Alert written to {self.path}")
        except Exception as e:
            logger.error(f"Failed to write alert to file: {e}")
            raise


# Global alert manager instance
_alert_manager: AlertManager | None = None


def initialize_alerts(config: dict[str, Any]) -> AlertManager:
    """Initialize global alert manager."""
    global _alert_manager
    _alert_manager = AlertManager(config)
    return _alert_manager


def get_alert_manager() -> AlertManager | None:
    """Get global alert manager instance."""
    return _alert_manager


def alert(severity: str, title: str, message: str, **kwargs):
    """Send alert via global alert manager."""
    manager = get_alert_manager()
    if manager:
        manager.alert(severity, title, message, **kwargs)
    else:
        logger.warning(f"No alert manager configured: {title}")
