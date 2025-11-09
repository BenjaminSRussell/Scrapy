"""
Pydantic models for type-safe data validation throughout the pipeline.

These models provide:
- Runtime validation
- Type safety
- Automatic serialization/deserialization
- Clear documentation of data structures
"""

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, HttpUrl, field_validator, ConfigDict


class URLRecord(BaseModel):
    """Base model for URL records across all pipeline stages."""

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str
    url_hash: str = Field(..., min_length=32, max_length=64)
    discovered_at: Optional[datetime] = None

    @field_validator('url_hash')
    @classmethod
    def validate_hash(cls, v: str) -> str:
        """Ensure hash is valid hexadecimal."""
        if not all(c in '0123456789abcdef' for c in v.lower()):
            raise ValueError('url_hash must be hexadecimal')
        return v.lower()


class Stage1Discovery(URLRecord):
    """Stage 1: URL discovery output."""

    is_heavy: bool = False
    is_dynamic: bool = False
    depth: int = Field(default=0, ge=0, le=10)
    parent_url: Optional[str] = None
    status: Literal["pending", "processing", "completed", "failed"] = "pending"
    queued_at: Optional[datetime] = None


class Stage2Analysis(URLRecord):
    """Stage 2: Page analysis output with quality metrics."""

    title: str = Field(default="", max_length=1000)
    word_count: int = Field(ge=0)
    content_length: int = Field(ge=0)
    html_length: int = Field(ge=0)
    text_to_html_ratio: float = Field(ge=0.0, le=1.0)
    is_low_quality: bool
    is_massive_doc: bool
    quality_score: float = Field(ge=0.0, le=1.0)
    text_content: str = Field(default="")
    keywords: list[str] = Field(default_factory=list)
    has_error: bool = False
    error_message: Optional[str] = None
    error_code: Optional[int] = None
    processed_at: datetime

    @field_validator('keywords')
    @classmethod
    def validate_keywords(cls, v: list[str]) -> list[str]:
        """Filter empty keywords."""
        return [k for k in v if k and k.strip()]


class Stage3Summary(URLRecord):
    """Stage 3: Summarization output for quality documents."""

    summary: str = Field(..., min_length=10, max_length=10000)
    word_count: int = Field(ge=0)
    keywords: list[str] = Field(default_factory=list)
    quality_score: float = Field(ge=0.0, le=1.0)
    timestamp: datetime

    @field_validator('summary')
    @classmethod
    def validate_summary(cls, v: str) -> str:
        """Ensure summary is meaningful."""
        if not v or len(v.strip()) < 10:
            raise ValueError('Summary must be at least 10 characters')
        return v.strip()


class Stage4LargeDocSummary(URLRecord):
    """Stage 4: Large document summary output."""

    summary: str = Field(..., min_length=10)
    content_type: str = Field(default="text/html")
    original_size: int = Field(ge=0)
    summary_size: int = Field(ge=0)
    compression_ratio: float = Field(ge=0.0, le=1.0)
    processed_at: datetime
    is_pdf: bool = False

    @field_validator('compression_ratio')
    @classmethod
    def validate_compression(cls, v: float, info) -> float:
        """Validate compression ratio makes sense."""
        if v > 1.0:
            raise ValueError('Compression ratio cannot exceed 1.0')
        return v


class QueueRecord(BaseModel):
    """Generic queue record for pipeline stages."""

    url: str
    url_hash: str
    status: Literal["pending", "processing", "completed", "failed"] = "pending"
    queued_at: datetime = Field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = Field(default=0, ge=0, le=10)
    error_message: Optional[str] = None

    @field_validator('retry_count')
    @classmethod
    def validate_retry_count(cls, v: int) -> int:
        """Ensure retry count is reasonable."""
        if v > 10:
            raise ValueError('Retry count exceeded maximum (10)')
        return v


class ErrorRecord(BaseModel):
    """Record for tracking errors and failures."""

    url: str
    url_hash: str
    stage: Literal["stage1", "stage2", "stage3", "stage4"]
    error_type: str
    error_message: str
    error_traceback: Optional[str] = None
    http_status_code: Optional[int] = None
    retry_count: int = 0
    is_retryable: bool = False
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    timestamp: datetime = Field(default_factory=datetime.now)
    context: dict = Field(default_factory=dict)


class HealthCheckResult(BaseModel):
    """Health check result for a component."""

    component: str
    status: Literal["healthy", "degraded", "unhealthy"]
    message: str
    response_time_ms: Optional[float] = None
    last_check: datetime = Field(default_factory=datetime.now)
    details: dict = Field(default_factory=dict)


class PipelineMetrics(BaseModel):
    """Pipeline performance metrics."""

    stage: str
    urls_processed: int = Field(ge=0)
    urls_failed: int = Field(ge=0)
    processing_time_seconds: float = Field(ge=0.0)
    average_latency_ms: float = Field(ge=0.0)
    throughput_per_minute: float = Field(ge=0.0)
    error_rate: float = Field(ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=datetime.now)

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.urls_processed + self.urls_failed
        if total == 0:
            return 0.0
        return self.urls_processed / total
