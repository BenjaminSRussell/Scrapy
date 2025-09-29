"""Tests for logging helpers."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from common import logging as logging_utils


@pytest.mark.parametrize("level", ["INFO", "DEBUG", "WARNING"])
def test_setup_logging_creates_logfile(tmp_path, level):
    log_file_dir = tmp_path / "logs"
    logging_utils.setup_logging(log_level=level, log_dir=log_file_dir)

    logger = logging.getLogger("test_logger")
    log_level = getattr(logging, level)
    logger.log(log_level, "sample message")

    file_path = log_file_dir / "pipeline.log"
    assert file_path.exists()
    content = file_path.read_text(encoding="utf-8")
    assert "sample message" in content

    # Reset logging handlers to avoid cross-test interference
    logging.shutdown()


def test_setup_logging_basic():
    """Test basic logging setup works"""
    logging_utils.setup_logging()
    logger = logging.getLogger("test")
    logger.info("test message")
    assert logger.level <= logging.INFO
