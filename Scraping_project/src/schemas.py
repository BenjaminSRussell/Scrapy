"""Pydantic schemas for high-integrity data validation.

This module defines canonical schemas for scraped items with strict type enforcement,
focusing on institutional costs and mandatory field validation.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator


class CategoryType(str, Enum):
    """Enumeration of valid content categories."""

    TUITION_FEES = "tuition_fees"
    HOUSING_COSTS = "housing_costs"
    FACULTY_RESEARCH = "faculty_research"
    STUDENT_LIFE = "student_life"
    ACADEMIC_PROGRAMS = "academic_programs"
    FINANCIAL_AID = "financial_aid"
    ADMISSIONS = "admissions"
    CAMPUS_FACILITIES = "campus_facilities"
    OTHER = "other"


class MediaType(str, Enum):
    """Enumeration of media types."""

    TEXT = "text"
    PDF = "pdf"
    AUDIO = "audio"
    VIDEO = "video"


class BaseRecordSchema(BaseModel):
    """Canonical schema for scraped items with institutional cost focus.

    This schema enforces data specificity, type integrity, and mandatory field
    presence for all extracted items. All cost fields use strict float validation
    with non-negative constraints.

    Attributes:
        url: Source webpage URL (mandatory)
        source_url: Original webpage URL for auditing
        media_url: PDF/Audio/Video file link (optional)
        media_type: Type of media content
        title: Page or document title (mandatory)
        content: Extracted text content
        publication_date: ISO 8601 compliant datetime string
        tuition_cost: Annual tuition cost in USD (≥ 0)
        housing_cost: Annual housing cost in USD (≥ 0)
        fees_cost: Additional fees in USD (≥ 0)
        total_cost: Total annual cost in USD (≥ 0)
        category_final: Final categorization from ZSC (mandatory post-classification)
        category_confidence: ZSC confidence score [0.0, 1.0]
        entity_id: Unique identifier for entity aggregation
        validation_status: Confirms passage through all integrity checks
        scraped_at_utc: UTC timestamp when scraped
        spider_name: Name of spider that scraped the item
    """

    # URL fields (source traceability)
    url: str = Field(..., min_length=1, description="Source webpage URL")
    source_url: str = Field(..., min_length=1, description="Original webpage URL")
    media_url: str | None = Field(None, description="PDF/Audio/Video file link")
    media_type: MediaType = Field(default=MediaType.TEXT, description="Type of media")

    # Content fields
    title: str = Field(..., min_length=1, max_length=500, description="Page/document title")
    content: str | None = Field(None, description="Extracted text content")

    # Temporal field
    publication_date: datetime = Field(..., description="ISO 8601 compliant publication date")

    # Institutional cost fields (strict non-negative floats)
    tuition_cost: float | None = Field(None, ge=0.0, description="Annual tuition cost in USD")
    housing_cost: float | None = Field(None, ge=0.0, description="Annual housing cost in USD")
    fees_cost: float | None = Field(None, ge=0.0, description="Additional fees in USD")
    total_cost: float | None = Field(None, ge=0.0, description="Total annual cost in USD")

    # Classification fields
    category_final: CategoryType | None = Field(None, description="Final ZSC category")
    category_confidence: float | None = Field(None, ge=0.0, le=1.0, description="ZSC confidence score [0.0, 1.0]")

    # Entity aggregation
    entity_id: str | None = Field(None, description="Unique entity identifier")

    # Validation status
    validation_status: bool = Field(default=False, description="Confirms successful validation")

    # Metadata fields
    scraped_at_utc: datetime | None = Field(None, description="Scraping timestamp UTC")
    spider_name: str | None = Field(None, description="Spider name")
    pipeline_version: str | None = Field(None, description="Pipeline version")

    # Recency scoring (added by RecencyScoringPipeline)
    recency_score: float | None = Field(None, ge=0.0, le=1.0, description="Temporal relevance score [0.0, 1.0]")

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "https://uconn.edu/tuition",
                "source_url": "https://uconn.edu/tuition",
                "media_url": None,
                "media_type": "text",
                "title": "UConn Tuition and Fees 2024-2025",
                "content": "Annual tuition for in-state students is $18,000...",
                "publication_date": "2024-01-15T00:00:00Z",
                "tuition_cost": 18000.0,
                "housing_cost": 12000.0,
                "fees_cost": 3000.0,
                "total_cost": 33000.0,
                "category_final": "tuition_fees",
                "category_confidence": 0.95,
                "entity_id": "uconn_undergraduate_tuition",
                "validation_status": True,
                "scraped_at_utc": "2024-01-20T10:30:00Z",
                "spider_name": "scout",
                "pipeline_version": "1.0.0",
                "recency_score": 0.99,
            }
        }
    }

    @field_validator("publication_date", mode="before")
    @classmethod
    def parse_publication_date(cls, v):
        """Parse publication date from various formats to datetime."""
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            # Handle ISO 8601 format with or without 'Z'
            if v.endswith("Z"):
                v = v[:-1] + "+00:00"
            return datetime.fromisoformat(v)
        raise ValueError(f"Invalid publication_date format: {v}")

    @field_validator("scraped_at_utc", mode="before")
    @classmethod
    def parse_scraped_at(cls, v):
        """Parse scraped_at_utc from various formats to datetime."""
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            if v.endswith("Z"):
                v = v[:-1] + "+00:00"
            return datetime.fromisoformat(v)
        raise ValueError(f"Invalid scraped_at_utc format: {v}")

    @model_validator(mode="after")
    def validate_costs(self):
        """Ensure cost consistency and calculate total if missing."""
        # If individual costs are present, calculate total if not provided
        cost_components = [
            self.tuition_cost or 0.0,
            self.housing_cost or 0.0,
            self.fees_cost or 0.0,
        ]

        if any(c > 0 for c in cost_components):
            calculated_total = sum(cost_components)
            if self.total_cost is None:
                self.total_cost = calculated_total
            else:
                # Validate that provided total matches sum (within 1% tolerance)
                if abs(self.total_cost - calculated_total) > (calculated_total * 0.01):
                    raise ValueError(
                        f"Total cost {self.total_cost} does not match sum of components {calculated_total}"
                    )

        return self


class ValidationFailureRecord(BaseModel):
    """Schema for validation failure events published to Kafka.

    This schema captures detailed error information when items fail validation,
    enabling downstream analysis and debugging.

    Attributes:
        url: The URL of the item that failed validation
        field_name: Name of the field that failed validation
        violation_rule: Description of the validation rule that was violated
        attempted_value: The value that failed validation (as string)
        error_message: Full error message from validation
        spider_name: Name of spider that yielded the item
        failed_at_utc: Timestamp when validation failed
        pipeline_version: Version of the validation pipeline
    """

    url: str = Field(..., description="URL of failed item")
    field_name: str = Field(..., description="Field that failed validation")
    violation_rule: str = Field(..., description="Violated validation rule")
    attempted_value: str | None = Field(None, description="Value that failed (as string)")
    error_message: str = Field(..., description="Full error message")
    spider_name: str = Field(..., description="Spider name")
    failed_at_utc: datetime = Field(default_factory=datetime.utcnow, description="Failure timestamp")
    pipeline_version: str = Field(default="1.0.0", description="Pipeline version")

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "https://example.com/invalid-cost",
                "field_name": "tuition_cost",
                "violation_rule": "must be non-negative float",
                "attempted_value": "-5000",
                "error_message": "Input should be greater than or equal to 0",
                "spider_name": "scout",
                "failed_at_utc": "2024-01-20T10:35:00Z",
                "pipeline_version": "1.0.0",
            }
        }
    }


class LowConfidenceRecord(BaseModel):
    """Schema for items with low classification confidence.

    Items falling below the confidence threshold are routed to this schema
    for human auditing and quality control.

    Attributes:
        url: Source URL of the item
        title: Item title
        content_preview: First 500 characters of content
        predicted_category: Category with highest confidence
        confidence_score: Actual confidence score
        threshold: Required confidence threshold
        needs_review: Flag indicating human review needed
        created_at_utc: Timestamp when routed for review
    """

    url: str = Field(..., description="Source URL")
    title: str = Field(..., description="Item title")
    content_preview: str = Field(..., max_length=500, description="Content preview")
    predicted_category: CategoryType = Field(..., description="Predicted category")
    confidence_score: float = Field(..., ge=0.0, le=1.0, description="Confidence score")
    threshold: float = Field(..., ge=0.0, le=1.0, description="Required threshold")
    needs_review: bool = Field(default=True, description="Needs human review")
    created_at_utc: datetime = Field(default_factory=datetime.utcnow, description="Creation timestamp")

    model_config = {
        "json_schema_extra": {
            "example": {
                "url": "https://example.com/ambiguous",
                "title": "Student Resources and Financial Information",
                "content_preview": "Various resources for students including...",
                "predicted_category": "student_life",
                "confidence_score": 0.72,
                "threshold": 0.85,
                "needs_review": True,
                "created_at_utc": "2024-01-20T10:40:00Z",
            }
        }
    }
