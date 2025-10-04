"""
Consolidated Data Schemas - All Pydantic Models

Merges all schema files into a single, organized module:
- schemas.py (dataclass versions)
- schemas_validated.py (Pydantic versions)
- config_schema.py (configuration schemas)
- warehouse_schema.py (SQL/warehouse schemas)
- datalake_schema.py (Delta Lake schemas)
"""

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ============================================================================
# Pipeline Data Schemas (Pydantic-validated)
# ============================================================================

class DiscoveryItem(BaseModel):
    """Stage 1 Discovery output with validation"""
    model_config = ConfigDict(extra='forbid')

    source_url: str = Field(min_length=1)
    discovered_url: str = Field(min_length=1)
    first_seen: str = Field(min_length=1)
    url_hash: str = Field(min_length=64, max_length=64)
    discovery_depth: int = Field(ge=0, le=10)
    discovery_source: str = Field(default="html_link")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    anchor_text: str | None = Field(default=None)
    is_same_domain: bool = Field(default=True)
    schema_version: str = Field(default="2.1")
    discovery_metadata: dict[str, str] | None = Field(default=None)

    @field_validator('source_url', 'discovered_url')
    @classmethod
    def validate_url_format(cls, v):
        if not v.startswith(('http://', 'https://')):
            raise ValueError(f"URL must start with http:// or https://: {v}")
        return v

    @field_validator('url_hash')
    @classmethod
    def validate_hash_format(cls, v):
        if not all(c in '0123456789abcdef' for c in v.lower()):
            raise ValueError(f"Invalid SHA-256 hash format: {v}")
        return v


class ValidationResult(BaseModel):
    """Stage 2 URL Validation with enhanced metadata"""
    model_config = ConfigDict(extra='forbid')

    url: str = Field(min_length=1)
    url_hash: str = Field(min_length=64, max_length=64)
    status_code: int = Field(ge=0, le=999)
    content_type: str = Field(default="")
    content_length: int = Field(ge=0)
    response_time: float = Field(ge=0.0)
    is_valid: bool
    error_message: str | None = Field(default=None)
    validated_at: str
    learned_optimizations: list[str] | None = Field(default=None)
    last_modified: str | None = Field(default=None)
    etag: str | None = Field(default=None)
    staleness_score: float = Field(default=0.0, ge=0.0, le=1.0)
    cache_control: str | None = Field(default=None)
    schema_version: str = Field(default="2.1")
    validation_method: str | None = Field(default=None)
    redirect_chain: list[str] | None = Field(default=None)
    server_headers: dict[str, str] | None = Field(default=None)
    network_metadata: dict[str, str] | None = Field(default=None)


class EnrichmentItem(BaseModel):
    """Stage 3 Enriched page data with NLP metadata"""
    model_config = ConfigDict(extra='forbid')

    url: str = Field(min_length=1)
    url_hash: str = Field(min_length=64, max_length=64)
    title: str = Field(default="")
    text_content: str = Field(default="")
    word_count: int = Field(ge=0)
    entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    content_tags: list[str] = Field(default_factory=list)
    has_pdf_links: bool = Field(default=False)
    has_audio_links: bool = Field(default=False)
    status_code: int = Field(ge=0)
    content_type: str = Field(default="")
    enriched_at: str
    expanded_keywords: dict[str, list[str]] | None = Field(default=None)
    qa_pairs: list[dict[str, str]] | None = Field(default=None)
    schema_version: str = Field(default="2.0")
    content_summary: str | None = Field(default=None)
    content_embedding: list[float] | None = Field(default=None)
    academic_relevance_score: float | None = Field(default=None)
    content_quality_score: float | None = Field(default=None)
    processing_pipeline_version: str | None = Field(default=None)
    source_discovery_method: str | None = Field(default=None)
    processing_metadata: dict[str, str] | None = Field(default=None)
    data_lineage: list[str] | None = Field(default=None)


class URLRecord(BaseModel):
    """Complete record combining all stages"""
    model_config = ConfigDict(extra='forbid')

    url: str
    source_url: str | None = None
    first_seen: str | None = None
    discovery_depth: int | None = None
    status_code: int | None = None
    content_type: str | None = None
    content_length: int | None = None
    response_time: float | None = None
    is_valid: bool | None = None
    validation_error: str | None = None
    validated_at: str | None = None
    title: str | None = None
    text_content: str | None = None
    word_count: int | None = None
    entities: list[str] | None = None
    keywords: list[str] | None = None
    content_tags: list[str] | None = None
    has_pdf_links: bool | None = None
    has_audio_links: bool | None = None
    enriched_at: str | None = None


@dataclass
class PipelineStats:
    """Statistics about pipeline execution"""
    stage: str
    start_time: str
    end_time: str | None = None
    input_count: int = 0
    output_count: int = 0
    error_count: int = 0
    duration_seconds: float | None = None


class SchemaRegistry:
    """Registry for managing schema versions and compatibility"""

    CURRENT_VERSIONS = {
        "DiscoveryItem": "2.1",
        "ValidationResult": "2.1",
        "EnrichmentItem": "2.0",
        "URLRecord": "1.0",
        "PipelineStats": "1.0"
    }

    COMPATIBLE_VERSIONS = {
        "DiscoveryItem": ["1.0", "2.0", "2.1"],
        "ValidationResult": ["1.0", "2.0", "2.1"],
        "EnrichmentItem": ["1.0", "2.0"],
        "URLRecord": ["1.0"],
        "PipelineStats": ["1.0"]
    }

    @classmethod
    def is_compatible(cls, schema_name: str, version: str) -> bool:
        return version in cls.COMPATIBLE_VERSIONS.get(schema_name, [])

    @classmethod
    def get_current_version(cls, schema_name: str) -> str:
        return cls.CURRENT_VERSIONS.get(schema_name, "1.0")


# ============================================================================
# Warehouse Schemas (SQL/PostgreSQL/SQLite)
# ============================================================================

class DatabaseType(Enum):
    """Database types"""
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


@dataclass
class PageRecord:
    """Page record for warehouse"""
    url: str
    url_hash: str
    title: str | None = None
    text_content: str | None = None
    word_count: int = 0
    content_type: str | None = None
    status_code: int | None = None
    has_pdf_links: bool = False
    has_audio_links: bool = False
    first_seen_at: str | None = None
    last_crawled_at: str | None = None
    crawl_version: int = 1
    is_current: bool = True


@dataclass
class EntityRecord:
    """Entity record for warehouse"""
    page_id: int
    entity_text: str
    entity_type: str | None = None
    confidence: float = 1.0
    source: str = "nlp"
    crawl_version: int = 1


@dataclass
class KeywordRecord:
    """Keyword record for warehouse"""
    page_id: int
    keyword_text: str
    frequency: int = 1
    relevance_score: float = 1.0
    source: str = "nlp"
    crawl_version: int = 1


@dataclass
class CategoryRecord:
    """Category record for warehouse"""
    page_id: int
    category_name: str
    category_path: str | None = None
    confidence_score: float = 1.0
    matched_keywords: str | None = None
    crawl_version: int = 1


@dataclass
class CrawlHistoryRecord:
    """Crawl history record"""
    url_hash: str
    crawl_timestamp: str
    status_code: int
    response_time: float
    content_hash: str | None = None


@dataclass
class VendorDataRecord:
    """Third-party vendor data record"""
    page_id: int
    vendor_name: str
    vendor_data: str  # JSON
    ingested_at: str


# ============================================================================
# Delta Lake Schemas
# ============================================================================

class TableType(Enum):
    """Types of tables in the data lake"""
    RAW = "raw"
    VALIDATED = "validated"
    ENRICHED = "enriched"
    METRICS = "metrics"
    GRAPH = "graph"


@dataclass
class DeltaLakeSchema:
    """Complete schema definition for Delta Lake tables"""

    RAW_URLS_SCHEMA = {
        'url': 'string',
        'url_hash': 'string',
        'discovered_from': 'string',
        'depth': 'int',
        'discovered_at': 'timestamp',
        'heuristic': 'string',
        'importance_score': 'double',
        'session_id': 'string'
    }

    VALIDATED_URLS_SCHEMA = {
        'url': 'string',
        'url_hash': 'string',
        'status_code': 'int',
        'content_type': 'string',
        'content_length': 'long',
        'response_time': 'double',
        'is_valid': 'boolean',
        'error_message': 'string',
        'validated_at': 'timestamp',
        'last_modified': 'string',
        'etag': 'string',
        'staleness_score': 'double',
        'cache_control': 'string',
        'validation_method': 'string',
        'redirect_chain': 'string',
        'server_headers': 'string',
        'network_metadata': 'string',
        'session_id': 'string'
    }

    ENRICHED_CONTENT_SCHEMA = {
        'url': 'string',
        'url_hash': 'string',
        'title': 'string',
        'description': 'string',
        'content_preview': 'string',
        'full_text': 'string',
        'entities': 'string',
        'keywords': 'string',
        'categories': 'string',
        'content_summary': 'string',
        'word_count': 'int',
        'unique_words': 'int',
        'sentence_count': 'int',
        'avg_sentence_length': 'double',
        'readability_score': 'double',
        'language': 'string',
        'page_type': 'string',
        'department': 'string',
        'campus': 'string',
        'audience': 'string',
        'extracted_at': 'timestamp',
        'processing_time': 'double',
        'schema_version': 'string',
        'nlp_model': 'string',
        'session_id': 'string',
        'year': 'int',
        'month': 'int',
        'day': 'int'
    }

    LINK_GRAPH_SCHEMA = {
        'source_url': 'string',
        'source_hash': 'string',
        'target_url': 'string',
        'target_hash': 'string',
        'link_text': 'string',
        'link_context': 'string',
        'discovered_at': 'timestamp',
        'importance_score': 'double',
        'session_id': 'string'
    }

    PERFORMANCE_METRICS_SCHEMA = {
        'timestamp': 'timestamp',
        'stage': 'string',
        'items_processed': 'long',
        'items_per_second': 'double',
        'cpu_percent': 'double',
        'memory_mb': 'double',
        'memory_percent': 'double',
        'thread_count': 'int',
        'elapsed_seconds': 'double',
        'session_id': 'string'
    }


@dataclass
class DataLakeConfig:
    """Configuration for Delta Lake warehouse"""
    BASE_PATH = "data/datalake"
    RAW_URLS_PATH = f"{BASE_PATH}/raw_urls"
    VALIDATED_URLS_PATH = f"{BASE_PATH}/validated_urls"
    ENRICHED_CONTENT_PATH = f"{BASE_PATH}/enriched_content"
    LINK_GRAPH_PATH = f"{BASE_PATH}/link_graph"
    PERFORMANCE_METRICS_PATH = f"{BASE_PATH}/performance_metrics"

    ENRICHED_PARTITION_COLS = ['year', 'month', 'day']
    METRICS_PARTITION_COLS = ['stage']

    DELTA_OPTIONS = {
        'mode': 'append',
        'mergeSchema': True,
        'overwriteSchema': False,
        'dataChange': True,
    }


# Utility function
def get_schema_for_table(table_type: TableType) -> dict[str, str]:
    """Get schema definition for a table type"""
    schemas = {
        TableType.RAW: DeltaLakeSchema.RAW_URLS_SCHEMA,
        TableType.VALIDATED: DeltaLakeSchema.VALIDATED_URLS_SCHEMA,
        TableType.ENRICHED: DeltaLakeSchema.ENRICHED_CONTENT_SCHEMA,
        TableType.GRAPH: DeltaLakeSchema.LINK_GRAPH_SCHEMA,
        TableType.METRICS: DeltaLakeSchema.PERFORMANCE_METRICS_SCHEMA
    }
    return schemas.get(table_type, {})
