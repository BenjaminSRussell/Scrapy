"""
Central Logging Utility - THE ONLY WAY TO LOG IN THIS PROJECT

This module provides the single, authoritative logging interface for the entire
UConn scraping pipeline. ALL logging must go through this module.

Usage:
    from src.common.logging import get_logger
    from src.common.log_events import LogEvent

    logger = get_logger(__name__)
    logger.log_event(LogEvent.URL_DISCOVERED, url=url, depth=1)
    logger.log_event(LogEvent.VALIDATION_ERROR, url=url, error="404")
"""

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from src.common.log_events import LogEvent, LogLevel, get_default_level

# Context variables for trace correlation
session_id_var: ContextVar[str | None] = ContextVar('session_id', default=None)
trace_id_var: ContextVar[str | None] = ContextVar('trace_id', default=None)
stage_var: ContextVar[str | None] = ContextVar('stage', default=None)

# Map LogLevel enum to Python logging levels
LEVEL_MAP = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARNING: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.CRITICAL: logging.CRITICAL,
}


class EventFormatter(logging.Formatter):
    """Formatter for event-based structured JSON logs with trace correlation"""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': datetime.fromtimestamp(record.created).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno
        }

        # Add event type if present
        if hasattr(record, 'event'):
            log_data['event'] = record.event

        # Add trace correlation IDs
        session_id = session_id_var.get()
        trace_id = trace_id_var.get()
        stage = stage_var.get()

        if session_id:
            log_data['session_id'] = session_id
        if trace_id:
            log_data['trace_id'] = trace_id
        if stage:
            log_data['stage'] = stage

        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        # Add event data if present
        if hasattr(record, 'event_data'):
            log_data['data'] = record.event_data

        return json.dumps(log_data, ensure_ascii=False, default=str)


class HumanReadableFormatter(logging.Formatter):
    """Human-readable console formatter with color support"""

    # Color codes for different log levels
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'
    }

    def format(self, record: logging.LogRecord) -> str:
        # Add color if terminal supports it
        if sys.stderr.isatty():
            color = self.COLORS.get(record.levelname, '')
            reset = self.COLORS['RESET']
        else:
            color = reset = ''

        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')

        # Build message parts
        parts = [
            f"{timestamp}",
            f"{color}[{record.levelname}]{reset}",
        ]

        # Add event type if present
        if hasattr(record, 'event'):
            parts.append(f"({record.event})")

        parts.append(f"{record.name}")
        parts.append(f"- {record.getMessage()}")

        # Add event data if present
        if hasattr(record, 'event_data') and record.event_data:
            data_str = ' '.join(f"{k}={v}" for k, v in record.event_data.items())
            parts.append(f"[{data_str}]")

        message = ' '.join(parts)

        # Add exception if present
        if record.exc_info:
            message += '\n' + self.formatException(record.exc_info)

        return message


class PipelineLogger(logging.LoggerAdapter):
    """
    THE PRIMARY LOGGING INTERFACE - use this for ALL logging in the project.

    This logger enforces consistent event-based logging with structured data.

    Examples:
        logger = get_logger(__name__)

        # Log with predefined event types
        logger.log_event(LogEvent.URL_DISCOVERED, url=url, depth=1)
        logger.log_event(LogEvent.VALIDATION_ERROR, url=url, error="404")
        logger.log_event(LogEvent.NLP_BATCH_COMPLETE, count=100, duration=5.2)

        # Traditional logging still works but is discouraged
        logger.info("Starting pipeline")  # OK but prefer log_event
    """

    def log_event(
        self,
        event: LogEvent,
        message: str | None = None,
        level: LogLevel | None = None,
        **data: Any
    ) -> None:
        """
        Log an event with structured data - THE PRIMARY LOGGING METHOD.

        Args:
            event: Event type from LogEvent enum (REQUIRED)
            message: Human-readable message (auto-generated if None)
            level: Log level (auto-determined from event if None)
            **data: Structured data fields (url, depth, count, etc.)

        Examples:
            logger.log_event(LogEvent.URL_DISCOVERED, url=url, depth=1)
            logger.log_event(
                LogEvent.VALIDATION_ERROR,
                message="URL validation failed",
                url=url,
                status_code=404,
                reason="Not Found"
            )
        """
        # Determine log level
        if level is None:
            level = get_default_level(event)

        # Generate message if not provided
        if message is None:
            message = event.value.replace('.', ' ').replace('_', ' ').title()

        # Create extra dict with event metadata
        extra = {
            'event': event.value,
            'event_data': data
        }

        # Log at appropriate level
        py_level = LEVEL_MAP[level]
        self.log(py_level, message, extra=extra)

    def process(self, msg, kwargs):
        """Process log messages to add context"""
        # Keep existing extra fields
        return msg, kwargs


def setup_logging(
    log_level: str = 'INFO',
    log_dir: Path | None = None,
    console_format: str = 'human',  # 'human' or 'json'
    file_format: str = 'json',      # 'human' or 'json'
    max_bytes: int = 10485760,      # 10MB
    backup_count: int = 3
):
    """
    Set up logging with event-based structured logging and rotation.

    This should be called ONCE at application startup (in main.py).

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Directory for log files (None = no file logging)
        console_format: Console output format ('human' or 'json')
        file_format: File output format ('human' or 'json')
        max_bytes: Maximum size of log file before rotation (default: 10MB)
        backup_count: Number of backup log files to keep

    Example:
        from pathlib import Path
        setup_logging(
            log_level='INFO',
            log_dir=Path('data/logs'),
            console_format='human',
            file_format='json'
        )
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Choose console formatter
    if console_format == 'json':
        console_formatter = EventFormatter()
    else:
        console_formatter = HumanReadableFormatter()

    # Choose file formatter
    if file_format == 'json':
        file_formatter = EventFormatter()
    else:
        file_formatter = HumanReadableFormatter()

    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handlers with rotation if log_dir provided
    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Main log file with rotation
        log_file = log_dir / 'pipeline.log'
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

        # Separate error log
        error_file = log_dir / 'error.log'
        error_handler = RotatingFileHandler(
            error_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(file_formatter)
        root_logger.addHandler(error_handler)

    # Configure third-party library logging levels to reduce noise
    for lib in ['scrapy', 'twisted', 'aiohttp', 'urllib3', 'asyncio']:
        logging.getLogger(lib).setLevel(logging.WARNING)


def get_logger(name: str) -> PipelineLogger:
    """
    Get a PipelineLogger instance - USE THIS IN EVERY MODULE.

    This is the standard way to get a logger in the project.

    Args:
        name: Logger name (use __name__ in modules)

    Returns:
        PipelineLogger instance with event-based logging support

    Example:
        from src.common.logging import get_logger
        from src.common.log_events import LogEvent

        logger = get_logger(__name__)
        logger.log_event(LogEvent.URL_DISCOVERED, url=url)
    """
    base_logger = logging.getLogger(name)
    return PipelineLogger(base_logger, {})


def set_session_id(session_id: str | None = None) -> str:
    """
    Set session ID for trace correlation.

    Call this at the start of a pipeline run to correlate all logs
    from the same execution.

    Args:
        session_id: Session ID (generates new UUID if None)

    Returns:
        Session ID

    Example:
        session_id = set_session_id()
        logger.info(f"Starting pipeline session {session_id}")
    """
    if session_id is None:
        session_id = str(uuid.uuid4())
    session_id_var.set(session_id)
    return session_id


def set_trace_id(trace_id: str | None = None) -> str:
    """
    Set trace ID for request correlation.

    Call this at the start of processing a URL to correlate all logs
    related to that URL across stages.

    Args:
        trace_id: Trace ID (generates new UUID if None)

    Returns:
        Trace ID

    Example:
        trace_id = set_trace_id()
        logger.log_event(LogEvent.URL_DISCOVERED, url=url)
        # ... processing ...
        clear_trace_context()  # Clear when done
    """
    if trace_id is None:
        trace_id = str(uuid.uuid4())
    trace_id_var.set(trace_id)
    return trace_id


def set_stage(stage: str) -> None:
    """
    Set current pipeline stage for context.

    Args:
        stage: Stage name ('stage1', 'stage2', 'stage3')

    Example:
        set_stage('stage1')
        logger.log_event(LogEvent.STAGE_START, stage='discovery')
    """
    stage_var.set(stage)


def get_session_id() -> str | None:
    """Get current session ID"""
    return session_id_var.get()


def get_trace_id() -> str | None:
    """Get current trace ID"""
    return trace_id_var.get()


def get_stage() -> str | None:
    """Get current stage"""
    return stage_var.get()


def clear_trace_context():
    """
    Clear trace context (trace_id only, keep session_id and stage).

    Call this after finishing processing a URL to avoid mixing traces.

    Example:
        set_trace_id()
        try:
            # Process URL
            logger.log_event(LogEvent.PAGE_ENRICHED, url=url)
        finally:
            clear_trace_context()
    """
    trace_id_var.set(None)


def clear_all_context():
    """Clear all context variables (session_id, trace_id, stage)"""
    session_id_var.set(None)
    trace_id_var.set(None)
    stage_var.set(None)


# Legacy compatibility - deprecated but kept for backward compatibility
class StructuredFormatter(EventFormatter):
    """DEPRECATED: Use EventFormatter instead"""
    pass


class StructuredLogger(PipelineLogger):
    """DEPRECATED: Use PipelineLogger instead"""
    pass


def get_structured_logger(name: str, **context) -> PipelineLogger:
    """DEPRECATED: Use get_logger() instead"""
    return get_logger(name)
