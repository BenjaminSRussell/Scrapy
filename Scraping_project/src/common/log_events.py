"""
Centralized Event Type Definitions for Logging.

This module defines ALL valid event types as an Enum to ensure consistency
across the entire codebase. NO other event types are allowed.

Usage:
    from src.common.log_events import LogEvent
    logger.log_event(LogEvent.URL_DISCOVERED, url=url, depth=depth)
"""

from enum import Enum


class LogEvent(str, Enum):
    """
    All valid logging event types in the UConn scraping pipeline.

    These are the ONLY allowed event types. Adding a new event requires:
    1. Adding it to this Enum
    2. Documenting it in the logging guide
    3. Using it via the logger.log_event() method
    """

    # ============================================================================
    # PIPELINE LIFECYCLE EVENTS
    # ============================================================================
    PIPELINE_START = "pipeline.start"
    PIPELINE_COMPLETE = "pipeline.complete"
    PIPELINE_FAILED = "pipeline.failed"
    PIPELINE_INTERRUPTED = "pipeline.interrupted"

    STAGE_START = "stage.start"
    STAGE_COMPLETE = "stage.complete"
    STAGE_FAILED = "stage.failed"

    # ============================================================================
    # STAGE 1: DISCOVERY EVENTS
    # ============================================================================
    URL_DISCOVERED = "discovery.url_discovered"
    URL_EXTRACTED = "discovery.url_extracted"
    URL_FILTERED = "discovery.url_filtered"
    URL_DUPLICATE = "discovery.url_duplicate"
    DOMAIN_BLOCKED = "discovery.domain_blocked"

    SEED_LOADED = "discovery.seed_loaded"
    HEURISTIC_MATCH = "discovery.heuristic_match"
    DEPTH_LIMIT = "discovery.depth_limit_reached"

    # ============================================================================
    # STAGE 2: VALIDATION EVENTS
    # ============================================================================
    URL_VALIDATED = "validation.url_validated"
    URL_INVALID = "validation.url_invalid"
    VALIDATION_ERROR = "validation.error"
    VALIDATION_TIMEOUT = "validation.timeout"
    VALIDATION_REDIRECT = "validation.redirect"

    HTTP_SUCCESS = "validation.http_success"
    HTTP_CLIENT_ERROR = "validation.http_client_error"
    HTTP_SERVER_ERROR = "validation.http_server_error"

    # ============================================================================
    # STAGE 3: ENRICHMENT EVENTS
    # ============================================================================
    PAGE_ENRICHED = "enrichment.page_enriched"
    CONTENT_EXTRACTED = "enrichment.content_extracted"
    ENTITIES_EXTRACTED = "enrichment.entities_extracted"
    KEYWORDS_EXTRACTED = "enrichment.keywords_extracted"
    CATEGORIES_ASSIGNED = "enrichment.categories_assigned"

    NLP_PROCESSING = "enrichment.nlp_processing"
    NLP_BATCH_COMPLETE = "enrichment.nlp_batch_complete"
    NLP_ERROR = "enrichment.nlp_error"

    CONTENT_TOO_SHORT = "enrichment.content_too_short"
    CONTENT_TYPE_UNSUPPORTED = "enrichment.content_type_unsupported"

    # ============================================================================
    # DATA STORAGE EVENTS
    # ============================================================================
    DATA_SAVED = "storage.data_saved"
    DATA_LOADED = "storage.data_loaded"
    FILE_CREATED = "storage.file_created"
    FILE_ROTATED = "storage.file_rotated"
    COMPRESSION_COMPLETE = "storage.compression_complete"

    DATABASE_CONNECTED = "storage.database_connected"
    DATABASE_ERROR = "storage.database_error"
    QUERY_EXECUTED = "storage.query_executed"

    # ============================================================================
    # PERFORMANCE & METRICS EVENTS
    # ============================================================================
    BATCH_PROCESSED = "metrics.batch_processed"
    QUEUE_STATUS = "metrics.queue_status"
    THROUGHPUT_MEASURED = "metrics.throughput"
    MEMORY_WARNING = "metrics.memory_warning"
    RATE_LIMIT_HIT = "metrics.rate_limit"

    # ============================================================================
    # ERROR & WARNING EVENTS
    # ============================================================================
    PARSING_ERROR = "error.parsing"
    NETWORK_ERROR = "error.network"
    CONFIGURATION_ERROR = "error.configuration"
    VALIDATION_ERROR_GENERIC = "error.validation"
    CHECKPOINT_ERROR = "error.checkpoint"

    DEPRECATION_WARNING = "warning.deprecation"
    CAPACITY_WARNING = "warning.capacity"
    QUALITY_WARNING = "warning.quality"

    # ============================================================================
    # SYSTEM EVENTS
    # ============================================================================
    CONFIG_LOADED = "system.config_loaded"
    CONFIG_VALIDATED = "system.config_validated"
    DIRECTORY_CREATED = "system.directory_created"
    CLEANUP_COMPLETE = "system.cleanup_complete"

    DEDUPLICATOR_INITIALIZED = "system.deduplicator_initialized"
    NLP_MODEL_LOADED = "system.nlp_model_loaded"
    FILTER_APPLIED = "system.filter_applied"

    # ============================================================================
    # MONITORING & DEBUGGING EVENTS
    # ============================================================================
    DEBUG_CHECKPOINT = "debug.checkpoint"
    SPIDER_OPENED = "debug.spider_opened"
    SPIDER_CLOSED = "debug.spider_closed"
    ITEM_SCRAPED = "debug.item_scraped"
    REQUEST_SCHEDULED = "debug.request_scheduled"


class LogLevel(str, Enum):
    """Standard logging levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# Event type to default log level mapping
EVENT_LOG_LEVELS = {
    # Pipeline events - INFO
    LogEvent.PIPELINE_START: LogLevel.INFO,
    LogEvent.PIPELINE_COMPLETE: LogLevel.INFO,
    LogEvent.PIPELINE_FAILED: LogLevel.ERROR,
    LogEvent.PIPELINE_INTERRUPTED: LogLevel.WARNING,

    LogEvent.STAGE_START: LogLevel.INFO,
    LogEvent.STAGE_COMPLETE: LogLevel.INFO,
    LogEvent.STAGE_FAILED: LogLevel.ERROR,

    # Discovery - DEBUG (frequent) / INFO (important)
    LogEvent.URL_DISCOVERED: LogLevel.DEBUG,
    LogEvent.URL_EXTRACTED: LogLevel.DEBUG,
    LogEvent.URL_FILTERED: LogLevel.DEBUG,
    LogEvent.URL_DUPLICATE: LogLevel.DEBUG,
    LogEvent.DOMAIN_BLOCKED: LogLevel.INFO,

    # Validation - INFO (status) / ERROR (failures)
    LogEvent.URL_VALIDATED: LogLevel.DEBUG,
    LogEvent.URL_INVALID: LogLevel.WARNING,
    LogEvent.VALIDATION_ERROR: LogLevel.ERROR,
    LogEvent.HTTP_CLIENT_ERROR: LogLevel.WARNING,
    LogEvent.HTTP_SERVER_ERROR: LogLevel.ERROR,

    # Enrichment - INFO (success) / ERROR (failures)
    LogEvent.PAGE_ENRICHED: LogLevel.DEBUG,
    LogEvent.ENTITIES_EXTRACTED: LogLevel.DEBUG,
    LogEvent.NLP_BATCH_COMPLETE: LogLevel.INFO,
    LogEvent.NLP_ERROR: LogLevel.ERROR,

    # Performance - INFO (normal) / WARNING (issues)
    LogEvent.BATCH_PROCESSED: LogLevel.INFO,
    LogEvent.QUEUE_STATUS: LogLevel.DEBUG,
    LogEvent.MEMORY_WARNING: LogLevel.WARNING,
    LogEvent.RATE_LIMIT_HIT: LogLevel.WARNING,

    # Errors - always ERROR or CRITICAL
    LogEvent.PARSING_ERROR: LogLevel.ERROR,
    LogEvent.NETWORK_ERROR: LogLevel.ERROR,
    LogEvent.CONFIGURATION_ERROR: LogLevel.CRITICAL,

    # System - INFO
    LogEvent.CONFIG_LOADED: LogLevel.INFO,
    LogEvent.NLP_MODEL_LOADED: LogLevel.INFO,
    LogEvent.DEDUPLICATOR_INITIALIZED: LogLevel.INFO,
}


def get_default_level(event: LogEvent) -> LogLevel:
    """Get the default log level for an event type."""
    return EVENT_LOG_LEVELS.get(event, LogLevel.INFO)
