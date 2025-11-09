from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

class CategoryType(str, Enum):

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

    TEXT = "text"
    PDF = "pdf"
    AUDIO = "audio"
    VIDEO = "video"

class BaseRecordSchema(BaseModel):

    url: str = Field(..., min_length=1, description="Source webpage URL")
    source_url: str = Field(..., min_length=1, description="Original webpage URL")
    media_url: str | None = Field(None, description="PDF/Audio/Video file link")
    media_type: MediaType = Field(default=MediaType.TEXT, description="Type of media")

    title: str = Field(..., min_length=1, max_length=500, description="Page/document title")
    content: str | None = Field(None, description="Extracted text content")

    publication_date: datetime = Field(..., description="ISO 8601 compliant publication date")

    tuition_cost: float | None = Field(None, ge=0.0, description="Annual tuition cost in USD")
    housing_cost: float | None = Field(None, ge=0.0, description="Annual housing cost in USD")
    fees_cost: float | None = Field(None, ge=0.0, description="Additional fees in USD")
    total_cost: float | None = Field(None, ge=0.0, description="Total annual cost in USD")

    category_final: CategoryType | None = Field(None, description="Final ZSC category")
    category_confidence: float | None = Field(None, ge=0.0, le=1.0, description="ZSC confidence score [0.0, 1.0]")

    entity_id: str | None = Field(None, description="Unique entity identifier")

    validation_status: bool = Field(default=False, description="Confirms successful validation")

    scraped_at_utc: datetime | None = Field(None, description="Scraping timestamp UTC")
    spider_name: str | None = Field(None, description="Spider name")
    pipeline_version: str | None = Field(None, description="Pipeline version")

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
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            if v.endswith("Z"):
                v = v[:-1] + "+00:00"
            return datetime.fromisoformat(v)
        raise ValueError(f"Invalid publication_date format: {v}")

    @field_validator("scraped_at_utc", mode="before")
    @classmethod
    def parse_scraped_at(cls, v):
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
                if abs(self.total_cost - calculated_total) > (calculated_total * 0.01):
                    raise ValueError(
                        f"Total cost {self.total_cost} does not match sum of components {calculated_total}"
                    )

        return self

class ValidationFailureRecord(BaseModel):

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
