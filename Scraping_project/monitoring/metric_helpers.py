"""
Helper functions for safely updating Prometheus metrics with robust exception handling.

This module provides wrappers around prometheus_client metric operations to:
- Prevent metric update failures from crashing the exporter
- Centralize exception handling and logging
- Simplify Delta Lake table metric updates
- Provide consistent error suppression across the codebase
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from prometheus_client import Counter, Gauge, Histogram

logger = logging.getLogger(__name__)


def safe_set_gauge(gauge: "Gauge", labels: dict[str, str], value: float) -> None:
    """
    Safely set a gauge metric value with exception handling.

    Args:
        gauge: Prometheus Gauge instance
        labels: Dictionary of label names to values
        value: Numeric value to set

    Returns:
        None (exceptions are logged and suppressed)
    """
    try:
        gauge.labels(**labels).set(value)
    except Exception as e:
        logger.debug(f"Failed to set gauge {gauge._name} with labels {labels}: {e}")


def safe_set_gauge_no_labels(gauge: "Gauge", value: float) -> None:
    """
    Safely set a gauge metric value (no labels) with exception handling.

    Args:
        gauge: Prometheus Gauge instance
        value: Numeric value to set

    Returns:
        None (exceptions are logged and suppressed)
    """
    try:
        gauge.set(value)
    except Exception as e:
        logger.debug(f"Failed to set gauge {gauge._name}: {e}")


def safe_inc_counter(counter: "Counter", labels: dict[str, str], amount: float = 1.0) -> None:
    """
    Safely increment a counter metric with exception handling.

    Args:
        counter: Prometheus Counter instance
        labels: Dictionary of label names to values
        amount: Amount to increment by (default: 1.0)

    Returns:
        None (exceptions are logged and suppressed)
    """
    try:
        counter.labels(**labels).inc(amount)
    except Exception as e:
        logger.debug(f"Failed to increment counter {counter._name} with labels {labels}: {e}")


def safe_observe_histogram(histogram: "Histogram", labels: dict[str, str], value: float) -> None:
    """
    Safely observe a histogram metric value with exception handling.

    Args:
        histogram: Prometheus Histogram instance
        labels: Dictionary of label names to values
        value: Numeric value to observe

    Returns:
        None (exceptions are logged and suppressed)
    """
    try:
        histogram.labels(**labels).observe(value)
    except Exception as e:
        logger.debug(f"Failed to observe histogram {histogram._name} with labels {labels}: {e}")


def update_delta_table_gauge(gauge: "Gauge", delta_manager: Any, table_name: str) -> int | None:
    """
    Update a gauge metric with the count of records in a Delta Lake table.

    Args:
        gauge: Prometheus Gauge instance to update
        delta_manager: DeltaLakeManager instance
        table_name: Name of the Delta Lake table

    Returns:
        Number of records counted, or None if table read failed
    """
    try:
        records = delta_manager.read(table_name)
        count = len(records) if records else 0
        gauge.labels(table=table_name).set(count)
        return count
    except Exception as e:
        logger.debug(f"Table {table_name} not found or empty: {e}")
        return None


def get_delta_table_size_bytes(delta_manager: Any, table_name: str) -> int | None:
    """
    Calculate the size of a Delta Lake table in bytes from parquet files.

    This uses an optimized approach that only sums parquet file sizes,
    avoiding expensive filesystem traversal of the _delta_log directory.

    Args:
        delta_manager: DeltaLakeManager instance (unused but kept for API consistency)
        table_name: Name of the Delta Lake table

    Returns:
        Size in bytes, or None if calculation failed
    """
    try:
        table_path = f"data/delta_lake/{table_name}"
        if not os.path.exists(table_path):
            return None

        # Only process parquet files to avoid walking the entire _delta_log directory
        parquet_files = [
            f for f in os.listdir(table_path) if f.endswith(".parquet") and os.path.isfile(os.path.join(table_path, f))
        ]

        if not parquet_files:
            return None

        # Quick size calculation from parquet files only
        size = sum(os.path.getsize(os.path.join(table_path, f)) for f in parquet_files)
        return size

    except Exception as e:
        logger.debug(f"Failed to calculate size for table {table_name}: {e}")
        return None


def update_delta_table_size_gauge(gauge: "Gauge", delta_manager: Any, table_name: str) -> int | None:
    """
    Update a gauge metric with the size in bytes of a Delta Lake table.

    Args:
        gauge: Prometheus Gauge instance to update
        delta_manager: DeltaLakeManager instance
        table_name: Name of the Delta Lake table

    Returns:
        Size in bytes, or None if calculation failed
    """
    size = get_delta_table_size_bytes(delta_manager, table_name)
    if size is not None:
        safe_set_gauge(gauge, {"table": table_name}, size)
    return size
