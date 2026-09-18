"""Offsite, Grafana, and SchemaValidation pipelines."""
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from confluent_kafka import Producer as KafkaProducer
else:
    KafkaProducer = Any

try:
    from confluent_kafka import Producer

    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False
    Producer = None

try:
    from pydantic import ValidationError

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    ValidationError = Exception  # type: ignore

from itemadapter import ItemAdapter
from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem, NotConfigured

from src.items import OffsiteCandidateItem

logger = logging.getLogger(__name__)

from src.queue_routing import is_queue_routing_item  # noqa: E402  # re-export for #608

class OffsiteCandidatePipeline:

    BATCH_SIZE = 100

    def __init__(self):
        from src.utils.delta import get_delta

        self.delta = get_delta()
        self.batch = []
        self.items_processed = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "OffsiteCandidatePipeline":
        pipeline = cls()

        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not isinstance(item, OffsiteCandidateItem):
            return item

        adapter = ItemAdapter(item)
        item_dict = adapter.asdict()

        self.batch.append(item_dict)
        self.items_processed += 1

        if len(self.batch) >= self.BATCH_SIZE:
            self._save_batch()

        if self.items_processed % 500 == 0:
            logger.info(f"Processed {self.items_processed} offsite candidates")

        return item

    def _save_batch(self):
        if not self.batch:
            return

        batch_size = len(self.batch)

        try:
            self.delta.write("stage1_offsite_candidates", self.batch, mode="append")
            logger.info(f" Saved {batch_size} offsite candidates to Delta Lake")

            try:
                from src.scrapy_prometheus import OFFSITE_CANDIDATES_SAVED

                if OFFSITE_CANDIDATES_SAVED:
                    OFFSITE_CANDIDATES_SAVED.labels(spider="scout").inc(batch_size)
            except ImportError:
                pass

            self.batch.clear()
        except Exception as e:
            logger.error(f"Failed to save offsite candidates batch: {e}")

    def spider_closed(self, spider: Spider) -> None:
        logger.info(f"Closing OffsiteCandidatePipeline for spider: {spider.name}")

        if self.batch:
            self._save_batch()

        logger.info(f"OffsiteCandidatePipeline stats - Total processed: {self.items_processed}")

class GrafanaSummaryPipeline:

    SAMPLE_RATE = 1000
    BATCH_SIZE = 10
    MAX_CONTENT_LENGTH = 500

    def __init__(self):
        self.items_processed = 0
        self.sampled_content = []
        import random

        self.random = random

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "GrafanaSummaryPipeline":
        pipeline = cls()
        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)
        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        if isinstance(item, OffsiteCandidateItem):
            return item

        self.items_processed += 1

        if self.items_processed % self.SAMPLE_RATE == 0:
            adapter = ItemAdapter(item)
            text_content = self._extract_text_content(adapter)

            if text_content:
                truncated_content = text_content[: self.MAX_CONTENT_LENGTH]
                if len(text_content) > self.MAX_CONTENT_LENGTH:
                    truncated_content += "..."

                self.sampled_content.append(truncated_content)
                logger.debug("Sampled content from item")

                if len(self.sampled_content) >= self.BATCH_SIZE:
                    self._generate_and_export_summary(spider)

        return item

    def _extract_text_content(self, adapter: ItemAdapter) -> str:
        text_fields = ["text", "content", "body", "description", "summary", "title"]

        for field in text_fields:
            if field in adapter and adapter.get(field):
                value = adapter.get(field)
                if isinstance(value, str):
                    return value.strip()

        if "url" in adapter:
            return f"URL: {adapter.get('url')}"

        return ""

    def _generate_and_export_summary(self, spider: Spider):
        if not self.sampled_content:
            return

        summary = " | ".join(self.sampled_content)

        MAX_SUMMARY_LENGTH = 2000
        if len(summary) > MAX_SUMMARY_LENGTH:
            summary = summary[:MAX_SUMMARY_LENGTH] + "..."

        try:
            from src.scrapy_prometheus import CRAWLER_CONTENT_SUMMARY

            if CRAWLER_CONTENT_SUMMARY:
                # Note: Prometheus Gauge doesn't accept string values directly
                CRAWLER_CONTENT_SUMMARY.labels(spider=spider.name).set(len(self.sampled_content))
                logger.info(f" Content Summary ({len(self.sampled_content)} samples): {summary[:200]}...")
        except ImportError:
            pass

        self.sampled_content = []

    def spider_closed(self, spider: Spider) -> None:
        logger.info(f"Closing GrafanaSummaryPipeline for spider: {spider.name}")

        if self.sampled_content:
            self._generate_and_export_summary(spider)

        logger.info(f"GrafanaSummaryPipeline stats - Total items processed: {self.items_processed}")

# ============================================================================
# ============================================================================

class SchemaValidationPipeline:

    PIPELINE_VERSION = "1.0.0"

    def __init__(
        self,
        enabled: bool = True,
        validation_failures_topic: str = "validation_failures",
    ):
        """Initialize the schema validation pipeline.

        Args:
            enabled: Whether validation is enabled
            validation_failures_topic: Kafka topic for validation failures
        """
        if not PYDANTIC_AVAILABLE:
            raise NotConfigured("Pydantic is required for SchemaValidationPipeline")

        self.enabled = enabled
        self.validation_failures_topic = validation_failures_topic
        self.items_validated = 0
        self.items_dropped = 0
        self.kafka_producer: KafkaProducer | None = None

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "SchemaValidationPipeline":
        enabled = crawler.settings.getbool("SCHEMA_VALIDATION_ENABLED", True)
        validation_failures_topic = crawler.settings.get("VALIDATION_FAILURES_TOPIC", "validation_failures")

        pipeline = cls(
            enabled=enabled,
            validation_failures_topic=validation_failures_topic,
        )

        crawler.signals.connect(pipeline.open_spider, signal=signals.spider_opened)
        crawler.signals.connect(pipeline.close_spider, signal=signals.spider_closed)

        return pipeline

    def open_spider(self, spider: Spider) -> None:
        if not KAFKA_AVAILABLE or not self.enabled:
            logger.warning("SchemaValidationPipeline: Kafka not available or disabled")
            return

        logger.info(f"Opening SchemaValidationPipeline for spider: {spider.name}")

        bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

        try:
            config = {
                "bootstrap.servers": bootstrap_servers,
                "linger.ms": 10,
                "compression.type": "snappy",
                "acks": 1,
            }

            security_protocol = os.getenv("KAFKA_SECURITY_PROTOCOL")
            if security_protocol:
                config["security.protocol"] = security_protocol

            sasl_mechanism = os.getenv("KAFKA_SASL_MECHANISM")
            if sasl_mechanism:
                config["sasl.mechanism"] = sasl_mechanism

            sasl_username = os.getenv("KAFKA_SASL_USERNAME")
            if sasl_username:
                config["sasl.username"] = sasl_username

            sasl_password = os.getenv("KAFKA_SASL_PASSWORD")
            if sasl_password:
                config["sasl.password"] = sasl_password

            self.kafka_producer = Producer(config)
            logger.info("Kafka producer initialized for validation failures")
        except Exception as e:
            logger.error(f"Failed to initialize Kafka producer: {e}")
            self.kafka_producer = None

    def close_spider(self, spider: Spider) -> None:
        logger.info(f"Closing SchemaValidationPipeline for spider: {spider.name}")

        if self.kafka_producer:
            try:
                remaining = self.kafka_producer.flush(timeout=30.0)
                if remaining > 0:
                    logger.warning(f"{remaining} validation failure messages not delivered")
            except Exception as e:
                logger.error(f"Error flushing Kafka producer: {e}")

        logger.info(
            f"SchemaValidationPipeline stats - Validated: {self.items_validated}, Dropped: {self.items_dropped}"
        )

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not self.enabled:
            return item

        if isinstance(item, OffsiteCandidateItem):
            return item

        adapter = ItemAdapter(item)
        item_dict = adapter.asdict()

        try:
            from src.schemas import BaseRecordSchema

            item_dict = self._coerce_currency_fields(item_dict)

            validated_record = BaseRecordSchema(**item_dict)

            validated_record.validation_status = True

            validated_dict = validated_record.model_dump(mode="json")
            for key, value in validated_dict.items():
                adapter[key] = value

            self.items_validated += 1

            if self.items_validated % 1000 == 0:
                logger.info(
                    f"SchemaValidation stats - Validated: {self.items_validated}, Dropped: {self.items_dropped}"
                )

            return item

        except ValidationError as e:
            self.items_dropped += 1

            self._publish_validation_failure(item_dict, e, spider)

            raise DropItem(f"Schema validation failed for {item_dict.get('url', 'unknown')}: {e}") from e

    def _coerce_currency_fields(self, item_dict: dict[str, Any]) -> dict[str, Any]:
        currency_fields = ["tuition_cost", "housing_cost", "fees_cost", "total_cost"]
        currency_pattern = re.compile(r"[\$£€¥,\s]+")

        for field in currency_fields:
            if field in item_dict and isinstance(item_dict[field], str):
                value = item_dict[field]
                cleaned = currency_pattern.sub("", value)
                try:
                    item_dict[field] = float(cleaned)
                except ValueError:
                    logger.warning(f"Failed to coerce {field}='{value}' to float, leaving as-is")

        return item_dict

    def _publish_validation_failure(self, item_dict: dict[str, Any], error: ValidationError, spider: Spider) -> None:
        if not self.kafka_producer:
            return

        try:
            from src.schemas import ValidationFailureRecord

            errors = error.errors()
            if not errors:
                return

            first_error = errors[0]
            field_name = ".".join(str(loc) for loc in first_error["loc"])
            violation_rule = first_error["type"]
            error_message = first_error["msg"]
            attempted_value = str(first_error.get("input", ""))

            failure_record = ValidationFailureRecord(
                url=item_dict.get("url", "unknown"),
                field_name=field_name,
                violation_rule=violation_rule,
                attempted_value=attempted_value,
                error_message=error_message,
                spider_name=spider.name,
                pipeline_version=self.PIPELINE_VERSION,
            )

            message = failure_record.model_dump_json()
            self.kafka_producer.produce(
                topic=self.validation_failures_topic,
                value=message.encode("utf-8"),
            )
            self.kafka_producer.poll(0)

            logger.warning(f"Published validation failure to Kafka: {field_name} - {error_message}")

        except Exception as e:
            logger.error(f"Failed to publish validation failure: {e}")
