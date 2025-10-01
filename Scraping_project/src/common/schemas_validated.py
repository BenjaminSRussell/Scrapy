"""
Pydantic-validated schemas for pipeline data with strict type checking.
Replaces dataclass schemas with validated models for inter-stage integrity.
"""

from typing import List, Dict, Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
import hashlib


class DiscoveryItem(BaseModel):
    """Stage 1 Discovery output with validation"""
    model_config = ConfigDict(extra='forbid')  # Reject unknown fields

    source_url: str = Field(
        min_length=1,
        description="URL where this link was discovered"
    )
    discovered_url: str = Field(
        min_length=1,
        description="The discovered URL"
    )
    first_seen: str = Field(
        min_length=1,
        description="ISO timestamp when first discovered"
    )
    url_hash: str = Field(
        min_length=64,
        max_length=64,
        description="SHA-256 hash of discovered_url"
    )
    discovery_depth: int = Field(
        ge=0,
        le=10,
        description="Crawl depth where discovered"
    )
    discovery_source: str = Field(
        default="html_link",
        description="Discovery method (html_link, ajax_endpoint, etc.)"
    )
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Confidence score for dynamic discoveries"
    )
    schema_version: str = Field(
        default="2.0",
        description="Schema version"
    )
    discovery_metadata: Optional[Dict[str, str]] = Field(
        default=None,
        description="Additional discovery context"
    )

    @field_validator('source_url', 'discovered_url')
    @classmethod
    def validate_url_format(cls, v):
        """Ensure URLs start with http:// or https://"""
        if not v.startswith(('http://', 'https://')):
            raise ValueError(f"URL must start with http:// or https://: {v}")
        return v

    @field_validator('first_seen')
    @classmethod
    def validate_iso_timestamp(cls, v):
        """Validate ISO timestamp format"""
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError as e:
            raise ValueError(f"Invalid ISO timestamp: {v}") from e
        return v

    @field_validator('url_hash')
    @classmethod
    def validate_hash_format(cls, v):
        """Ensure hash is valid SHA-256 hex string"""
        if not all(c in '0123456789abcdef' for c in v.lower()):
            raise ValueError(f"Invalid SHA-256 hash format: {v}")
        return v

    @model_validator(mode='after')
    def validate_hash_matches_url(self):
        """Verify url_hash is correct SHA-256 of discovered_url"""
        expected_hash = hashlib.sha256(self.discovered_url.encode('utf-8')).hexdigest()
        if self.url_hash != expected_hash:
            raise ValueError(
                f"url_hash mismatch: expected {expected_hash} for {self.discovered_url}, "
                f"got {self.url_hash}"
            )
        return self


class ValidationResult(BaseModel):
    """Stage 2 Validation output with validation"""
    model_config = ConfigDict(extra='forbid')

    url: str = Field(
        min_length=1,
        description="Validated URL (may differ from original due to redirects)"
    )
    url_hash: str = Field(
        min_length=64,
        max_length=64,
        description="SHA-256 hash of original URL"
    )
    status_code: int = Field(
        ge=0,
        le=999,
        description="HTTP status code"
    )
    content_type: str = Field(
        description="Content-Type header value"
    )
    content_length: int = Field(
        ge=0,
        description="Content length in bytes"
    )
    response_time: float = Field(
        ge=0.0,
        description="Response time in seconds"
    )
    is_valid: bool = Field(
        description="Whether URL passed validation"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Error message if validation failed"
    )
    validated_at: str = Field(
        min_length=1,
        description="ISO timestamp of validation"
    )
    learned_optimizations: Optional[List[str]] = Field(
        default=None,
        description="Learned optimization hints"
    )
    schema_version: str = Field(
        default="2.0",
        description="Schema version"
    )
    validation_method: Optional[str] = Field(
        default=None,
        description="Validation method (HEAD, GET, etc.)"
    )
    redirect_chain: Optional[List[str]] = Field(
        default=None,
        description="URL redirect chain"
    )
    server_headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Relevant server headers"
    )
    network_metadata: Optional[Dict[str, str]] = Field(
        default=None,
        description="Network metadata (DNS time, etc.)"
    )

    @field_validator('url')
    @classmethod
    def validate_url_format(cls, v):
        """Ensure URL starts with http:// or https://"""
        if not v.startswith(('http://', 'https://')):
            raise ValueError(f"URL must start with http:// or https://: {v}")
        return v

    @field_validator('validated_at')
    @classmethod
    def validate_iso_timestamp(cls, v):
        """Validate ISO timestamp format"""
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError as e:
            raise ValueError(f"Invalid ISO timestamp: {v}") from e
        return v

    @field_validator('url_hash')
    @classmethod
    def validate_hash_format(cls, v):
        """Ensure hash is valid SHA-256 hex string"""
        if not all(c in '0123456789abcdef' for c in v.lower()):
            raise ValueError(f"Invalid SHA-256 hash format: {v}")
        return v

    @model_validator(mode='after')
    def validate_error_message_consistency(self):
        """If is_valid is False, error_message should be present"""
        if not self.is_valid and not self.error_message:
            raise ValueError("error_message must be provided when is_valid is False")
        return self


class EnrichmentItem(BaseModel):
    """Stage 3 Enrichment output with validation"""
    model_config = ConfigDict(extra='forbid')

    url: str = Field(
        min_length=1,
        description="Enriched URL"
    )
    url_hash: str = Field(
        min_length=64,
        max_length=64,
        description="SHA-256 hash of URL"
    )
    title: str = Field(
        description="Page title"
    )
    text_content: str = Field(
        description="Extracted text content"
    )
    word_count: int = Field(
        ge=0,
        description="Word count"
    )
    entities: List[str] = Field(
        description="Extracted entities"
    )
    keywords: List[str] = Field(
        description="Extracted keywords"
    )
    content_tags: List[str] = Field(
        description="Content classification tags"
    )
    has_pdf_links: bool = Field(
        description="Whether page has PDF links"
    )
    has_audio_links: bool = Field(
        description="Whether page has audio links"
    )
    status_code: int = Field(
        ge=0,
        le=999,
        description="HTTP status code"
    )
    content_type: str = Field(
        description="Content-Type"
    )
    enriched_at: str = Field(
        min_length=1,
        description="ISO timestamp of enrichment"
    )
    schema_version: str = Field(
        default="2.0",
        description="Schema version"
    )
    content_summary: Optional[str] = Field(
        default=None,
        description="AI-generated summary"
    )
    content_embedding: Optional[List[float]] = Field(
        default=None,
        description="Vector embedding"
    )
    academic_relevance_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Academic relevance score (0-1)"
    )
    content_quality_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Content quality score (0-1)"
    )
    processing_pipeline_version: Optional[str] = Field(
        default=None,
        description="Pipeline version"
    )
    source_discovery_method: Optional[str] = Field(
        default=None,
        description="How URL was discovered"
    )
    processing_metadata: Optional[Dict[str, str]] = Field(
        default=None,
        description="Processing metadata"
    )
    data_lineage: Optional[List[str]] = Field(
        default=None,
        description="Chain of processing steps"
    )

    @field_validator('url')
    @classmethod
    def validate_url_format(cls, v):
        """Ensure URL starts with http:// or https://"""
        if not v.startswith(('http://', 'https://')):
            raise ValueError(f"URL must start with http:// or https://: {v}")
        return v

    @field_validator('enriched_at')
    @classmethod
    def validate_iso_timestamp(cls, v):
        """Validate ISO timestamp format"""
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError as e:
            raise ValueError(f"Invalid ISO timestamp: {v}") from e
        return v

    @field_validator('url_hash')
    @classmethod
    def validate_hash_format(cls, v):
        """Ensure hash is valid SHA-256 hex string"""
        if not all(c in '0123456789abcdef' for c in v.lower()):
            raise ValueError(f"Invalid SHA-256 hash format: {v}")
        return v

    @model_validator(mode='after')
    def validate_word_count_consistency(self):
        """Verify word_count matches text_content"""
        actual_word_count = len(self.text_content.split()) if self.text_content else 0
        # Allow some tolerance for word count calculation differences
        if abs(self.word_count - actual_word_count) > 10:
            raise ValueError(
                f"word_count mismatch: field says {self.word_count}, "
                f"actual count is {actual_word_count}"
            )
        return self


class PipelineStats(BaseModel):
    """Statistics about pipeline execution"""
    model_config = ConfigDict(extra='forbid')

    stage: str = Field(
        min_length=1,
        description="Pipeline stage name"
    )
    start_time: str = Field(
        min_length=1,
        description="Start timestamp"
    )
    end_time: Optional[str] = Field(
        default=None,
        description="End timestamp"
    )
    input_count: int = Field(
        default=0,
        ge=0,
        description="Number of input items"
    )
    output_count: int = Field(
        default=0,
        ge=0,
        description="Number of output items"
    )
    error_count: int = Field(
        default=0,
        ge=0,
        description="Number of errors"
    )
    duration_seconds: Optional[float] = Field(
        default=None,
        ge=0.0,
        description="Execution duration"
    )

    @field_validator('start_time', 'end_time')
    @classmethod
    def validate_iso_timestamp(cls, v):
        """Validate ISO timestamp format"""
        if v is None:
            return v
        try:
            datetime.fromisoformat(v.replace('Z', '+00:00'))
        except ValueError as e:
            raise ValueError(f"Invalid ISO timestamp: {v}") from e
        return v


class SchemaRegistry:
    """Registry for managing schema versions and compatibility"""

    CURRENT_VERSIONS = {
        "DiscoveryItem": "2.0",
        "ValidationResult": "2.0",
        "EnrichmentItem": "2.0",
        "PipelineStats": "1.0"
    }

    COMPATIBLE_VERSIONS = {
        "DiscoveryItem": ["1.0", "2.0"],
        "ValidationResult": ["1.0", "2.0"],
        "EnrichmentItem": ["1.0", "2.0"],
        "PipelineStats": ["1.0"]
    }

    # Map schema names to Pydantic models
    SCHEMA_MODELS = {
        "DiscoveryItem": DiscoveryItem,
        "ValidationResult": ValidationResult,
        "EnrichmentItem": EnrichmentItem,
        "PipelineStats": PipelineStats
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
    def get_model(cls, schema_name: str) -> type[BaseModel]:
        """Get Pydantic model for schema"""
        return cls.SCHEMA_MODELS.get(schema_name)

    @classmethod
    def validate_record(cls, schema_name: str, data: Dict[str, Any]) -> BaseModel:
        """Validate a record against its schema"""
        model = cls.get_model(schema_name)
        if not model:
            raise ValueError(f"Unknown schema: {schema_name}")
        return model(**data)
