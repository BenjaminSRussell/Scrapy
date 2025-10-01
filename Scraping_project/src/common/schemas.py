# TODO: Add more detailed schema validation, such as checking the types of the fields and ensuring that required fields are present.
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class DiscoveryItem:
    """Item returned by Stage 1 Discovery Spider with schema versioning"""
    source_url: str
    discovered_url: str
    first_seen: str
    url_hash: str
    discovery_depth: int
    discovery_source: str = "html_link"
    confidence: float = 1.0  # 0.0-1.0 confidence score for dynamic discoveries

    # TODO: This schema versioning is very basic. It should be improved to support more complex schema evolution scenarios, such as renaming fields or changing their types.
    # Schema versioning and provenance
    schema_version: str = "2.0"
    discovery_metadata: dict[str, str] | None = None  # Additional discovery context


@dataclass
class ValidationResult:
    """Result from Stage 2 URL Validation with enhanced metadata"""
    url: str
    url_hash: str  # critical for stage linkage - without this everything breaks
    status_code: int
    content_type: str
    content_length: int
    response_time: float
    is_valid: bool
    error_message: str | None
    validated_at: str
    learned_optimizations: list[str] | None = None

    # Schema versioning and enhanced validation metadata
    schema_version: str = "2.0"
    validation_method: str | None = None  # HEAD, GET, etc.
    redirect_chain: list[str] | None = None  # Track URL redirects
    server_headers: dict[str, str] | None = None  # Relevant server headers
    network_metadata: dict[str, str] | None = None  # DNS resolution time, etc.


@dataclass
class EnrichmentItem:
    """Item returned by Stage 3 Enrichment Spider with model-ready enhancements"""
    url: str
    url_hash: str
    title: str
    text_content: str
    word_count: int
    entities: list[str]
    keywords: list[str]
    content_tags: list[str]
    has_pdf_links: bool
    has_audio_links: bool
    status_code: int
    content_type: str
    enriched_at: str

    # Model-ready enhancements
    schema_version: str = "2.0"
    content_summary: str | None = None  # AI-generated summary for training data
    content_embedding: list[float] | None = None  # Vector embedding for similarity search
    academic_relevance_score: float | None = None  # 0.0-1.0 relevance to academic content
    content_quality_score: float | None = None  # 0.0-1.0 overall content quality

    # Provenance and lineage tracking
    processing_pipeline_version: str | None = None
    source_discovery_method: str | None = None  # How this URL was discovered
    processing_metadata: dict[str, str] | None = None  # Processing timestamps, versions, etc.
    data_lineage: list[str] | None = None  # Chain of processing steps


@dataclass
class URLRecord:
    """Complete record combining all stages"""
    url: str

    # Discovery info
    source_url: str | None = None
    first_seen: str | None = None
    discovery_depth: int | None = None

    # Validation info
    status_code: int | None = None
    content_type: str | None = None
    content_length: int | None = None
    response_time: float | None = None
    is_valid: bool | None = None
    validation_error: str | None = None
    validated_at: str | None = None

    # Enrichment info
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


# TODO: Add support for generating documentation from the schemas, such as a data dictionary or a JSON schema.
class SchemaRegistry:
    """Registry for managing schema versions and compatibility"""

    # Current schema versions
    CURRENT_VERSIONS = {
        "DiscoveryItem": "2.0",
        "ValidationResult": "2.0",
        "EnrichmentItem": "2.0",
        "URLRecord": "1.0",
        "PipelineStats": "1.0"
    }

    # Schema compatibility matrix - which versions can be read by current code
    COMPATIBLE_VERSIONS = {
        "DiscoveryItem": ["1.0", "2.0"],
        "ValidationResult": ["1.0", "2.0"],
        "EnrichmentItem": ["1.0", "2.0"],
        "URLRecord": ["1.0"],
        "PipelineStats": ["1.0"]
    }

    @classmethod
    def is_compatible(cls, schema_name: str, version: str) -> bool:
        """Check if a schema version is compatible with current code"""
        return version in cls.COMPATIBLE_VERSIONS.get(schema_name, [])

    @classmethod
    def get_current_version(cls, schema_name: str) -> str:
        """Get the current version for a schema"""
        return cls.CURRENT_VERSIONS.get(schema_name, "1.0")

    @classmethod
    def upgrade_discovery_item(cls, data: dict[str, any]) -> dict[str, any]:
        """Upgrade DiscoveryItem from v1.0 to v2.0"""
        if data.get("schema_version", "1.0") == "1.0":
            data["schema_version"] = "2.0"
            data["discovery_metadata"] = None
        return data

    @classmethod
    def upgrade_validation_result(cls, data: dict[str, any]) -> dict[str, any]:
        """Upgrade ValidationResult from v1.0 to v2.0"""
        if data.get("schema_version", "1.0") == "1.0":
            data["schema_version"] = "2.0"
            data["validation_method"] = None
            data["redirect_chain"] = None
            data["server_headers"] = None
            data["network_metadata"] = None
        return data

    @classmethod
    def upgrade_enrichment_item(cls, data: dict[str, any]) -> dict[str, any]:
        """Upgrade EnrichmentItem from v1.0 to v2.0"""
        if data.get("schema_version", "1.0") == "1.0":
            data["schema_version"] = "2.0"
            data["content_summary"] = None
            data["content_embedding"] = None
            data["academic_relevance_score"] = None
            data["content_quality_score"] = None
            data["processing_pipeline_version"] = None
            data["source_discovery_method"] = None
            data["processing_metadata"] = None
            data["data_lineage"] = None
        return data
