import logging
import pytest
from src.common.retry_middleware import classify_status, calculate_backoff
from src.scrapy_prometheus import format_metric
from src.common.logging_utils import get_log_formatter

def test_classify_status():
    """Tests the classify_status function."""
    assert classify_status(200) == 'success'
    assert classify_status(404) == 'fail'
    assert classify_status(503) == 'retry'
    assert classify_status(302) == 'success'

def test_calculate_backoff():
    """Tests the calculate_backoff function."""
    # Test with default values
    assert 2.0 <= calculate_backoff(1) <= 2.2
    assert 4.0 <= calculate_backoff(2) <= 4.4
    # Test with custom values
    assert 9.0 <= calculate_backoff(2, base=3.0) <= 9.9
    # Test max_delay
    assert calculate_backoff(10, max_delay=100.0) == 100.0

def test_format_metric():
    """Tests the format_metric function."""
    assert format_metric('test_metric') == 'scrapy_test_metric'
    assert format_metric('test_metric', prefix='custom') == 'custom_test_metric'

def test_get_log_formatter():
    """Tests the get_log_formatter function."""
    formatter = get_log_formatter()
    assert isinstance(formatter, logging.Formatter)
    # Create a log record and format it to check the output
    record = logging.LogRecord(
        name='test_logger',
        level=logging.INFO,
        pathname='/path/to/file.py',
        lineno=10,
        msg='Test message',
        args=(),
        exc_info=None
    )
    # The exact timestamp will vary, so we check the other parts of the message
    formatted_message = formatter.format(record)
    assert '[INFO] [test_logger] Test message' in formatted_message
