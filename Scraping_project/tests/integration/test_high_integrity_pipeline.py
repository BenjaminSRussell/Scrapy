from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.common.scoring_metrics import calculate_decay_score, calculate_weighted_average
from src.pipelines import (
    AggregationPipeline,
    RecencyScoringPipeline,
    SchemaValidationPipeline,
)
from src.schemas import BaseRecordSchema, CategoryType, ValidationFailureRecord

class TestSchemaValidation:

    def test_valid_institutional_cost_record(self):
        record_data = {
            "url": "https://uconn.edu/tuition",
            "source_url": "https://uconn.edu/tuition",
            "media_url": None,
            "media_type": "text",
            "title": "UConn Tuition 2024-2025",
            "content": "Annual tuition for in-state students is $18,000",
            "publication_date": "2024-01-15T00:00:00Z",
            "tuition_cost": 18000.0,
            "housing_cost": 12000.0,
            "fees_cost": 3000.0,
            "total_cost": 33000.0,
            "category_final": "tuition_fees",
            "category_confidence": 0.95,
            "entity_id": "uconn_undergraduate_tuition",
            "validation_status": True,
        }

        record = BaseRecordSchema(**record_data)

        assert record.url == "https://uconn.edu/tuition"
        assert record.tuition_cost == 18000.0
        assert record.total_cost == 33000.0
        assert record.validation_status is True
        assert record.category_final == CategoryType.TUITION_FEES

    def test_currency_string_coercion(self):
        # Note: Coercion happens in the pipeline, not the schema
        record_data = {
            "url": "https://example.com",
            "source_url": "https://example.com",
            "title": "Test",
            "publication_date": "2024-01-15T00:00:00Z",
            "tuition_cost": 25000.0,
            "housing_cost": 12500.0,
        }

        record = BaseRecordSchema(**record_data)

        assert record.tuition_cost == 25000.0
        assert record.housing_cost == 12500.0

    def test_negative_cost_validation_fails(self):
        record_data = {
            "url": "https://example.com",
            "source_url": "https://example.com",
            "title": "Test",
            "publication_date": "2024-01-15T00:00:00Z",
            "tuition_cost": -5000.0,
        }

        with pytest.raises(Exception):
            BaseRecordSchema(**record_data)

    def test_automatic_total_cost_calculation(self):
        record_data = {
            "url": "https://example.com",
            "source_url": "https://example.com",
            "title": "Test",
            "publication_date": "2024-01-15T00:00:00Z",
            "tuition_cost": 10000.0,
            "housing_cost": 8000.0,
            "fees_cost": 2000.0,
        }

        record = BaseRecordSchema(**record_data)

        assert record.total_cost == 20000.0

    def test_publication_date_parsing(self):
        record1 = BaseRecordSchema(
            url="https://example.com",
            source_url="https://example.com",
            title="Test",
            publication_date="2024-01-15T10:30:00Z",
        )
        assert isinstance(record1.publication_date, datetime)

        record2 = BaseRecordSchema(
            url="https://example.com",
            source_url="https://example.com",
            title="Test",
            publication_date="2024-01-15T10:30:00+00:00",
        )
        assert isinstance(record2.publication_date, datetime)

        now = datetime.now(UTC)
        record3 = BaseRecordSchema(
            url="https://example.com",
            source_url="https://example.com",
            title="Test",
            publication_date=now,
        )
        assert record3.publication_date == now

class TestRecencyScoring:

    def test_decay_score_recent_content(self):
        now = datetime(2024, 1, 20, tzinfo=UTC)
        yesterday = now - timedelta(days=1)

        score = calculate_decay_score(
            publication_date=yesterday,
            reference_date=now,
            decay_constant=0.01,
        )

        assert 0.99 < score <= 1.0

    def test_decay_score_old_content(self):
        now = datetime(2024, 1, 20, tzinfo=UTC)
        year_ago = now - timedelta(days=365)

        score = calculate_decay_score(
            publication_date=year_ago,
            reference_date=now,
            decay_constant=0.01,
        )

        assert 0.02 < score < 0.03

    def test_decay_score_future_date_raises_error(self):
        now = datetime(2024, 1, 20, tzinfo=UTC)
        future = now + timedelta(days=1)

        with pytest.raises(ValueError, match="in the future"):
            calculate_decay_score(
                publication_date=future,
                reference_date=now,
                decay_constant=0.01,
            )

    def test_decay_score_string_input(self):
        reference = "2024-01-20T00:00:00Z"
        publication = "2024-01-10T00:00:00Z"

        score = calculate_decay_score(
            publication_date=publication,
            reference_date=reference,
            decay_constant=0.01,
        )

        assert 0.9 < score < 1.0

    def test_weighted_average_with_recency(self):
        values = [100.0, 80.0, 60.0]
        recency_scores = [1.0, 0.8, 0.5]

        avg = calculate_weighted_average(values, recency_scores)

        assert 80 < avg < 90

    def test_weighted_average_validation_errors(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            calculate_weighted_average([], [])

        with pytest.raises(ValueError, match="same length"):
            calculate_weighted_average([1.0, 2.0], [1.0])

        with pytest.raises(ValueError, match="must be in range"):
            calculate_weighted_average([1.0], [1.5])

class TestPipelineIntegration:

    @pytest.fixture
    def mock_spider(self):
        spider = MagicMock()
        spider.name = "test_spider"
        return spider

    @pytest.fixture
    def sample_item(self):
        return {
            "url": "https://example.com/tuition",
            "source_url": "https://example.com/tuition",
            "title": "Tuition Information",
            "content": "Annual tuition is $50,000",
            "publication_date": "2024-01-15T00:00:00Z",
            "tuition_cost": "$50,000",
            "housing_cost": "$15,000",
            "fees_cost": "$5,000",
        }

    def test_schema_validation_pipeline_success(self, mock_spider, sample_item):
        with patch("src.pipelines.KAFKA_AVAILABLE", True), patch("src.pipelines.PYDANTIC_AVAILABLE", True):
            pipeline = SchemaValidationPipeline(
                enabled=True,
                validation_failures_topic="test_failures",
            )
            pipeline.kafka_producer = None

            result = pipeline.process_item(sample_item, mock_spider)

            assert result.get("validation_status") is True

            assert result["tuition_cost"] == 50000.0
            assert result["housing_cost"] == 15000.0

    def test_recency_scoring_pipeline(self, mock_spider):
        pipeline = RecencyScoringPipeline(
            decay_constant=0.01,
            default_score=0.5,
        )

        item = {
            "url": "https://example.com",
            "publication_date": datetime(2024, 1, 15, tzinfo=UTC),
        }

        result = pipeline.process_item(item, mock_spider)

        assert "recency_score" in result
        assert 0.0 <= result["recency_score"] <= 1.0

    def test_aggregation_pipeline_grouping(self, mock_spider):
        pipeline = AggregationPipeline(enabled=True)

        items = [
            {
                "entity_id": "uconn_tuition",
                "title": "UConn Tuition 2024",
                "recency_score": 1.0,
            },
            {
                "entity_id": "uconn_tuition",
                "title": "UConn Tuition 2023",
                "recency_score": 0.5,
            },
            {
                "entity_id": "yale_tuition",
                "title": "Yale Tuition 2024",
                "recency_score": 0.9,
            },
        ]

        for item in items:
            pipeline.process_item(item, mock_spider)

        assert len(pipeline.entity_groups) == 2
        assert len(pipeline.entity_groups["uconn_tuition"]) == 2
        assert len(pipeline.entity_groups["yale_tuition"]) == 1

        pipeline.close_spider(mock_spider)

        uconn_items = pipeline.entity_groups["uconn_tuition"]
        assert uconn_items[0]["recency_score"] > uconn_items[1]["recency_score"]

    def test_full_pipeline_flow(self, mock_spider):
        with patch("src.pipelines.KAFKA_AVAILABLE", True), patch("src.pipelines.PYDANTIC_AVAILABLE", True):
            validation_pipeline = SchemaValidationPipeline(enabled=True)
            validation_pipeline.kafka_producer = None

            scoring_pipeline = RecencyScoringPipeline(decay_constant=0.01)
            aggregation_pipeline = AggregationPipeline(enabled=True)

            item = {
                "url": "https://example.com",
                "source_url": "https://example.com",
                "title": "Test Item",
                "publication_date": "2024-01-15T00:00:00Z",
                "tuition_cost": "$25,000",
                "entity_id": "test_entity",
            }

            item = validation_pipeline.process_item(item, mock_spider)
            item = scoring_pipeline.process_item(item, mock_spider)
            item = aggregation_pipeline.process_item(item, mock_spider)

            assert item["validation_status"] is True
            assert "recency_score" in item
            assert aggregation_pipeline.entity_groups["test_entity"][0] == item

class TestValidationFailureRecord:

    def test_validation_failure_record_creation(self):
        record = ValidationFailureRecord(
            url="https://example.com/bad",
            field_name="tuition_cost",
            violation_rule="ge",
            attempted_value="-5000",
            error_message="Input should be greater than or equal to 0",
            spider_name="test_spider",
            pipeline_version="1.0.0",
        )

        assert record.url == "https://example.com/bad"
        assert record.field_name == "tuition_cost"
        assert record.violation_rule == "ge"
        assert isinstance(record.failed_at_utc, datetime)

    def test_validation_failure_record_serialization(self):
        record = ValidationFailureRecord(
            url="https://example.com/bad",
            field_name="tuition_cost",
            violation_rule="ge",
            error_message="Validation failed",
            spider_name="test_spider",
        )

        json_str = record.model_dump_json()

        assert "https://example.com/bad" in json_str
        assert "tuition_cost" in json_str
        assert "Validation failed" in json_str

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
