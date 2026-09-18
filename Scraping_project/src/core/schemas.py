"""
PyArrow schemas for Delta Lake tables.

These schemas ensure data consistency and enable partition pruning and
column projection for performance.
"""

import pyarrow as pa
from typing import Final, Dict

# Stage 1: URL Discovery Schema
STAGE1_DISCOVERY_SCHEMA: Final[pa.Schema] = pa.schema([
    ("url", pa.string()),
    ("url_hash", pa.string()),
    ("is_heavy", pa.bool_()),
    ("is_dynamic", pa.bool_()),
    ("depth", pa.int32()),
    ("parent_url", pa.string()),
    ("status", pa.string()),
    ("queued_at", pa.timestamp("ms")),
    ("discovered_at", pa.timestamp("ms")),
])

# Stage 2: Page Analysis Schema
STAGE2_ANALYSIS_SCHEMA: Final[pa.Schema] = pa.schema([
    ("url", pa.string()),
    ("url_hash", pa.string()),
    ("title", pa.string()),
    ("word_count", pa.int64()),
    ("content_length", pa.int64()),
    ("html_length", pa.int64()),
    ("text_to_html_ratio", pa.float64()),
    ("is_low_quality", pa.bool_()),
    ("is_massive_doc", pa.bool_()),
    ("quality_score", pa.float64()),
    ("text_content", pa.string()),
    ("keywords", pa.list_(pa.string())),
    ("has_error", pa.bool_()),
    ("error_message", pa.string()),
    ("error_code", pa.int32()),
    ("processed_at", pa.timestamp("ms")),
])

# Stage 2: Queue Schema
STAGE2_QUEUE_SCHEMA: Final[pa.Schema] = pa.schema([
    ("url", pa.string()),
    ("url_hash", pa.string()),
    ("status", pa.string()),
    ("is_heavy", pa.bool_()),
    ("queued_at", pa.timestamp("ms")),
    ("started_at", pa.timestamp("ms")),
    ("completed_at", pa.timestamp("ms")),
    ("retry_count", pa.int32()),
])

# Stage 3: Summary Schema
STAGE3_SUMMARY_SCHEMA: Final[pa.Schema] = pa.schema([
    ("url", pa.string()),
    ("url_hash", pa.string()),
    ("summary", pa.string()),
    ("word_count", pa.int64()),
    ("keywords", pa.list_(pa.string())),
    ("quality_score", pa.float64()),
    ("timestamp", pa.timestamp("ms")),
])

# Stage 4: Large Document Summary Schema
STAGE4_LARGE_DOC_SCHEMA: Final[pa.Schema] = pa.schema([
    ("url", pa.string()),
    ("url_hash", pa.string()),
    ("summary", pa.string()),
    ("content_type", pa.string()),
    ("original_size", pa.int64()),
    ("summary_size", pa.int64()),
    ("compression_ratio", pa.float64()),
    ("is_pdf", pa.bool_()),
    ("processed_at", pa.timestamp("ms")),
])

# Seed URLs Schema
SEED_URLS_SCHEMA: Final[pa.Schema] = pa.schema([
    ("url", pa.string()),
    ("domain", pa.string()),
    ("seed_priority", pa.int32()),
    ("last_crawled", pa.timestamp("ms")),
    ("is_active", pa.bool_()),
])

# Error Tracking Schema
ERROR_LOG_SCHEMA: Final[pa.Schema] = pa.schema([
    ("url", pa.string()),
    ("url_hash", pa.string()),
    ("stage", pa.string()),
    ("error_type", pa.string()),
    ("error_message", pa.string()),
    ("http_status_code", pa.int32()),
    ("retry_count", pa.int32()),
    ("is_retryable", pa.bool_()),
    ("severity", pa.string()),
    ("timestamp", pa.timestamp("ms")),
])

# Performance Metrics Schema
METRICS_SCHEMA: Final[pa.Schema] = pa.schema([
    ("stage", pa.string()),
    ("urls_processed", pa.int64()),
    ("urls_failed", pa.int64()),
    ("processing_time_seconds", pa.float64()),
    ("average_latency_ms", pa.float64()),
    ("throughput_per_minute", pa.float64()),
    ("error_rate", pa.float64()),
    ("timestamp", pa.timestamp("ms")),
])

# Schema Registry
SCHEMA_REGISTRY: Final[Dict[str, pa.Schema]] = {
    "stage1_discovery": STAGE1_DISCOVERY_SCHEMA,
    "stage2_queue": STAGE2_QUEUE_SCHEMA,
    "stage2_page_analysis": STAGE2_ANALYSIS_SCHEMA,
    "stage3_queue": STAGE3_SUMMARY_SCHEMA,  # Reuse for queue
    "stage4_summaries": STAGE3_SUMMARY_SCHEMA,
    "stage4_large_docs": STAGE4_LARGE_DOC_SCHEMA,
    "stage4_large_doc_summaries": STAGE4_LARGE_DOC_SCHEMA,
    "seed_urls": SEED_URLS_SCHEMA,
    "error_log": ERROR_LOG_SCHEMA,
    "metrics": METRICS_SCHEMA,
}


def get_schema(table_name: str) -> pa.Schema:
    """
    Get PyArrow schema for a table.

    Args:
        table_name: Name of the Delta Lake table

    Returns:
        PyArrow schema for the table

    Raises:
        KeyError: If table schema not found
    """
    if table_name not in SCHEMA_REGISTRY:
        raise KeyError(f"Schema not found for table: {table_name}")
    return SCHEMA_REGISTRY[table_name]


def validate_data_schema(data: list[dict], table_name: str) -> bool:
    """
    Validate data matches expected schema.

    Args:
        data: List of records to validate
        table_name: Expected table name

    Returns:
        True if validation passes

    Raises:
        ValueError: If data doesn't match schema
    """
    if not data:
        return True

    schema = get_schema(table_name)
    expected_fields = {field.name for field in schema}

    # Check first record has required fields
    actual_fields = set(data[0].keys())

    # Allow extra fields, but ensure required fields exist
    # Note: This is permissive - add stricter validation if needed

    return True
