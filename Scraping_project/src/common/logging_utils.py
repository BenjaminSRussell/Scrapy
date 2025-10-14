import logging
import sys

def get_log_formatter() -> logging.Formatter:
    """
    Returns a standardized log formatter for consistent log output.
    Format: [TIMESTAMP] [LEVEL] [LOGGER_NAME] MESSAGE
    """
    return logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

def setup_logging(level: int = logging.INFO):
    """
    Configures the root logger to use the standard formatter.
    This ensures that all loggers in the application will inherit
    the same logging configuration.
    Args:
        level: The logging level to set for the root logger.
    """
    # Get the root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove any existing handlers to avoid duplicate logs
    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # Add a new handler with the standard formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(get_log_formatter())
    root_logger.addHandler(handler)
