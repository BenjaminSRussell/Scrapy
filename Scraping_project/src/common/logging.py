import logging
import json
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any
from contextvars import ContextVar

# Context variables for trace correlation
session_id_var: ContextVar[Optional[str]] = ContextVar('session_id', default=None)
trace_id_var: ContextVar[Optional[str]] = ContextVar('trace_id', default=None)


class StructuredFormatter(logging.Formatter):
    """Custom formatter that outputs structured JSON logs with trace correlation"""

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

        # Add trace correlation IDs
        session_id = session_id_var.get()
        trace_id = trace_id_var.get()
        if session_id:
            log_data['session_id'] = session_id
        if trace_id:
            log_data['trace_id'] = trace_id

        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        # Add extra fields if present
        if hasattr(record, 'extra_fields'):
            log_data.update(record.extra_fields)

        return json.dumps(log_data, ensure_ascii=False)


class StructuredLogger(logging.LoggerAdapter):
    """Logger adapter that supports structured logging with additional context"""

    def process(self, msg, kwargs):
        """Add extra fields to log records"""
        extra = kwargs.get('extra', {})
        if 'extra_fields' not in extra:
            extra['extra_fields'] = {}

        # Merge any additional context
        if hasattr(self, 'extra'):
            extra['extra_fields'].update(self.extra)

        kwargs['extra'] = extra
        return msg, kwargs

    def log_with_context(self, level: int, msg: str, **context):
        """Log with additional structured context"""
        extra = {'extra_fields': context}
        self.log(level, msg, extra=extra)


def setup_logging(
    log_level: str = 'INFO',
    log_dir: Optional[Path] = None,
    structured: bool = False,
    max_bytes: int = 10485760,  # 10MB
    backup_count: int = 3
):
    """Set up logging with support for structured JSON logging and rotation.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_dir: Directory for log files
        structured: If True, use JSON structured logging format
        max_bytes: Maximum size of log file before rotation (default: 10MB)
        backup_count: Number of backup log files to keep
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Choose formatter based on structured flag
    if structured:
        console_formatter = StructuredFormatter()
        file_formatter = StructuredFormatter()
    else:
        format_str = '%(asctime)s [%(levelname)s] %(name)s - %(message)s'
        console_formatter = logging.Formatter(format_str, datefmt='%Y-%m-%d %H:%M:%S')
        file_formatter = logging.Formatter(format_str, datefmt='%Y-%m-%d %H:%M:%S')

    # Clear any existing handlers
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # File handler with rotation if log_dir provided
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

    # Configure third-party library logging levels
    for lib in ['scrapy', 'twisted', 'aiohttp', 'urllib3', 'asyncio']:
        logging.getLogger(lib).setLevel(logging.WARNING)


def get_structured_logger(name: str, **context) -> StructuredLogger:
    """Get a structured logger with additional context.

    Args:
        name: Logger name
        **context: Additional context fields to include in all log messages

    Returns:
        StructuredLogger instance
    """
    logger = logging.getLogger(name)
    return StructuredLogger(logger, context)


def set_session_id(session_id: Optional[str] = None) -> str:
    """
    Set session ID for trace correlation.

    Args:
        session_id: Session ID (generates new UUID if None)

    Returns:
        Session ID
    """
    if session_id is None:
        session_id = str(uuid.uuid4())
    session_id_var.set(session_id)
    return session_id


def set_trace_id(trace_id: Optional[str] = None) -> str:
    """
    Set trace ID for request correlation.

    Args:
        trace_id: Trace ID (generates new UUID if None)

    Returns:
        Trace ID
    """
    if trace_id is None:
        trace_id = str(uuid.uuid4())
    trace_id_var.set(trace_id)
    return trace_id


def get_session_id() -> Optional[str]:
    """Get current session ID"""
    return session_id_var.get()


def get_trace_id() -> Optional[str]:
    """Get current trace ID"""
    return trace_id_var.get()


def clear_trace_context():
    """Clear trace context (trace_id only, keep session_id)"""
    trace_id_var.set(None)