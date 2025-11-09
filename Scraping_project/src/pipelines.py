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

class DataValidationPipeline:

    def __init__(self, required_fields: list[str] | None = None):
        self.required_fields = required_fields or ["url"]
        self.items_validated = 0
        self.items_dropped = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "DataValidationPipeline":
        required_fields = crawler.settings.getlist("VALIDATION_REQUIRED_FIELDS", ["url"])
        return cls(required_fields=required_fields)

    def process_item(self, item: Any, spider: Spider) -> Any:
        adapter = ItemAdapter(item)

        required_fields = self.required_fields
        if isinstance(item, OffsiteCandidateItem):
            required_fields = ["external_url"]

        for field in required_fields:
            if field not in adapter:
                self.items_dropped += 1
                raise DropItem(
                    f"Missing required field '{field}' in item from spider '{spider.name}'. Item: {dict(adapter)}"
                )

            value = adapter.get(field)

            if isinstance(value, str) and not value.strip():
                self.items_dropped += 1
                raise DropItem(
                    f"Required field '{field}' is empty or whitespace in item from spider '{spider.name}'. "
                    f"Item: {dict(adapter)}"
                )

            if value is None:
                self.items_dropped += 1
                raise DropItem(
                    f"Required field '{field}' is None in item from spider '{spider.name}'. Item: {dict(adapter)}"
                )

        self.items_validated += 1

        if self.items_validated % 1000 == 0:
            logger.info(f"Validation stats - Validated: {self.items_validated}, Dropped: {self.items_dropped}")

        return item

class DataCleansingPipeline:

    CURRENCY_PATTERN = re.compile(r"[\$£€¥]?\s*([0-9,]+\.?[0-9]*)")

    def __init__(self):
        self.items_cleansed = 0

    def process_item(self, item: Any, spider: Spider) -> Any:
        adapter = ItemAdapter(item)

        for field_name in adapter.field_names():
            value = adapter.get(field_name)

            if value is None:
                continue

            if isinstance(value, str):
                cleaned = value.strip()
                normalized_value: str | float = cleaned

                if field_name in ("category", "type", "status"):
                    normalized_value = cleaned.lower()

                if field_name in ("price", "cost", "amount"):
                    normalized_value = self._parse_currency(cleaned)

                adapter[field_name] = normalized_value

            elif isinstance(value, list):
                adapter[field_name] = [item.strip() if isinstance(item, str) else item for item in value]

        self.items_cleansed += 1

        if self.items_cleansed % 1000 == 0:
            logger.info(f"Cleansed {self.items_cleansed} items")

        return item

    def _parse_currency(self, value: str) -> float | str:
        match = self.CURRENCY_PATTERN.search(value)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                logger.warning(f"Failed to parse currency value: {value}")
                return value
        return value

class MetadataPipeline:

    PIPELINE_VERSION = "1.0.0"

    def __init__(self):
        self.items_enriched = 0

    def process_item(self, item: Any, spider: Spider) -> Any:
        adapter = ItemAdapter(item)

        adapter["scraped_at_utc"] = datetime.utcnow().isoformat() + "Z"
        adapter["spider_name"] = spider.name
        adapter["pipeline_version"] = self.PIPELINE_VERSION

        self.items_enriched += 1

        if self.items_enriched % 1000 == 0:
            logger.info(f"Enriched {self.items_enriched} items with metadata")

        return item

class KafkaPipeline:

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        producer_config: dict[str, Any] | None = None,
    ):
        """Initialize the Kafka pipeline.

        Args:
            bootstrap_servers: Comma-separated list of Kafka broker addresses
            topic: Target Kafka topic name
            producer_config: Optional additional producer configuration
        """
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.producer_config = producer_config or {}
        self.producer: KafkaProducer | None = None
        self.messages_sent = 0
        self.messages_failed = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "KafkaPipeline":
        if not KAFKA_AVAILABLE:
            logger.warning("Kafka pipeline disabled - confluent_kafka not installed")
            raise NotConfigured("confluent_kafka library not available")

        bootstrap_servers = crawler.settings.get("KAFKA_BOOTSTRAP_SERVERS")
        if not bootstrap_servers:
            raise NotConfigured("KAFKA_BOOTSTRAP_SERVERS setting is required")

        topic = crawler.settings.get("KAFKA_TOPIC")
        if not topic:
            raise NotConfigured("KAFKA_TOPIC setting is required")

        producer_config = crawler.settings.get("KAFKA_PRODUCER_CONFIG", {})

        pipeline = cls(
            bootstrap_servers=bootstrap_servers,
            topic=topic,
            producer_config=producer_config,
        )

        crawler.signals.connect(pipeline.open_spider, signal=signals.spider_opened)
        crawler.signals.connect(pipeline.close_spider, signal=signals.spider_closed)

        return pipeline

    def open_spider(self, spider: Spider) -> None:
        logger.info(f"Opening Kafka pipeline for spider: {spider.name}")

        config = {
            "bootstrap.servers": self.bootstrap_servers,
            "linger.ms": 10,
            "batch.size": 16384,
            "compression.type": "snappy",
            "acks": 1,
            "retries": 3,
            "max.in.flight.requests.per.connection": 5,
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

        config.update(self.producer_config)

        try:
            self.producer = Producer(config)
            logger.info(f"Kafka producer initialized: {self.bootstrap_servers}")
        except Exception as e:
            logger.error(f"Failed to initialize Kafka producer: {e}")
            raise

    def close_spider(self, spider: Spider) -> None:
        logger.info(f"Closing Kafka pipeline for spider: {spider.name}")

        if self.producer:
            try:
                remaining = self.producer.flush(timeout=30.0)
                if remaining > 0:
                    logger.warning(f"{remaining} messages were not delivered before timeout")

                logger.info(f"Kafka pipeline stats - Sent: {self.messages_sent}, Failed: {self.messages_failed}")
            except Exception as e:
                logger.error(f"Error flushing Kafka producer: {e}")

    def delivery_report(self, err: Any, msg: Any) -> None:
        if err is not None:
            self.messages_failed += 1
            logger.error(f"Message delivery failed: {err}")
        else:
            self.messages_sent += 1
            if self.messages_sent % 1000 == 0:
                logger.info(f"Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}")

    def process_item(self, item: Any, spider: Spider) -> Any:
        try:
            item_dict = ItemAdapter(item).asdict()

            message_value = json.dumps(item_dict, ensure_ascii=False, default=str)

            if self.producer is None:
                raise RuntimeError("Kafka producer is not initialized")

            self.producer.produce(
                topic=self.topic,
                value=message_value.encode("utf-8"),
                callback=self.delivery_report,
            )

            self.producer.poll(0)

        except Exception as e:
            logger.error(f"Error processing item for Kafka: {e}")
            raise DropItem(f"Failed to publish item to Kafka: {e}") from e

        return item

class QueueItemPipeline:

    BATCH_SIZE = 100

    def __init__(self):
        from src.common.delta_lake import get_delta_manager

        self.delta = get_delta_manager()
        self.js_queue_batch = []
        self.stage2_queue_batch = []
        self.items_processed = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "QueueItemPipeline":
        pipeline = cls()

        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not isinstance(item, dict):
            return item

        target_spider = item.get("target_spider")
        target_stage = item.get("target_stage")

        if target_spider == "javascript":
            self.js_queue_batch.append(item)
            self.items_processed += 1

            if len(self.js_queue_batch) >= self.BATCH_SIZE:
                self._save_js_queue_batch()

        elif target_stage == "stage2":
            self.stage2_queue_batch.append(item)
            self.items_processed += 1

            if len(self.stage2_queue_batch) >= self.BATCH_SIZE:
                self._save_stage2_queue_batch()
        else:
            logger.warning(f"QueueItemPipeline: Received a dict item with no routing metadata: {item}")

        if self.items_processed % 500 == 0:
            logger.info(
                f"[QUEUE] Processed {self.items_processed} queue items "
                f"(JS: {len(self.js_queue_batch)}, Stage2: {len(self.stage2_queue_batch)})"
            )

        return item

    def _save_js_queue_batch(self):
        if not self.js_queue_batch:
            return

        batch_size = len(self.js_queue_batch)

        try:
            self.delta.write("js_spider_queue", self.js_queue_batch, mode="append")
            logger.info(f"✅ Saved {batch_size} items to js_spider_queue")
            self.js_queue_batch.clear()
        except Exception as e:
            logger.error(f"Failed to save JS queue batch: {e}")

    def _save_stage2_queue_batch(self):
        if not self.stage2_queue_batch:
            return

        batch_size = len(self.stage2_queue_batch)

        try:
            self.delta.write("stage2_queue", self.stage2_queue_batch, mode="append")
            logger.info(f"✅ Saved {batch_size} items to stage2_queue")
            self.stage2_queue_batch.clear()
        except Exception as e:
            logger.error(f"Failed to save Stage 2 queue batch: {e}")

    def spider_closed(self, spider: Spider) -> None:
        logger.info(f"[QUEUE] Closing QueueItemPipeline for spider: {spider.name}")

        if self.js_queue_batch:
            self._save_js_queue_batch()

        if self.stage2_queue_batch:
            self._save_stage2_queue_batch()

        logger.info(f"[QUEUE] Pipeline stats - Total processed: {self.items_processed}")

class OffsiteCandidatePipeline:

    BATCH_SIZE = 100

    def __init__(self):
        from src.common.delta_lake import get_delta_manager

        self.delta = get_delta_manager()
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
            logger.info(f"✅ Saved {batch_size} offsite candidates to Delta Lake")

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
                logger.debug(f"Sampled content from item

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
                logger.info(f"📊 Content Summary ({len(self.sampled_content)} samples): {summary[:200]}...")
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

class RecencyScoringPipeline:

    def __init__(
        self,
        decay_constant: float = 0.01,
        default_score: float = 0.5,
    ):
        """Initialize the recency scoring pipeline.

        Args:
            decay_constant: Decay rate parameter (k). Higher = faster decay.
            default_score: Score for items missing publication_date
        """
        self.decay_constant = decay_constant
        self.default_score = default_score
        self.items_scored = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "RecencyScoringPipeline":
        decay_constant = crawler.settings.getfloat("RECENCY_DECAY_CONSTANT", 0.01)
        default_score = crawler.settings.getfloat("RECENCY_DEFAULT_SCORE", 0.5)

        return cls(
            decay_constant=decay_constant,
            default_score=default_score,
        )

    def process_item(self, item: Any, spider: Spider) -> Any:
        if isinstance(item, OffsiteCandidateItem):
            return item

        adapter = ItemAdapter(item)

        publication_date = adapter.get("publication_date")

        if publication_date:
            try:
                from src.common.scoring_metrics import calculate_decay_score

                score = calculate_decay_score(
                    publication_date=publication_date,
                    decay_constant=self.decay_constant,
                )
                adapter["recency_score"] = score
            except Exception as e:
                logger.warning(f"Failed to calculate recency score for {adapter.get('url')}: {e}")
                adapter["recency_score"] = self.default_score
        else:
            adapter["recency_score"] = self.default_score

        self.items_scored += 1

        if self.items_scored % 1000 == 0:
            logger.info(f"RecencyScoring: Scored {self.items_scored} items")

        return item

class AggregationPipeline:

    def __init__(
        self,
        enabled: bool = True,
        output_topic: str = "entity_summaries",
    ):
        """Initialize the aggregation pipeline.

        Args:
            enabled: Whether aggregation is enabled
            output_topic: Kafka topic for entity summaries
        """
        self.enabled = enabled
        self.output_topic = output_topic
        self.entity_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.items_aggregated = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "AggregationPipeline":
        enabled = crawler.settings.getbool("AGGREGATION_ENABLED", True)
        output_topic = crawler.settings.get("AGGREGATION_OUTPUT_TOPIC", "entity_summaries")

        pipeline = cls(enabled=enabled, output_topic=output_topic)

        crawler.signals.connect(pipeline.close_spider, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not self.enabled:
            return item

        if isinstance(item, OffsiteCandidateItem):
            return item

        adapter = ItemAdapter(item)
        entity_id = adapter.get("entity_id")

        if entity_id:
            item_dict = adapter.asdict()
            self.entity_groups[entity_id].append(item_dict)
            self.items_aggregated += 1

        return item

    def close_spider(self, spider: Spider) -> None:
        if not self.enabled:
            return

        logger.info(f"Closing AggregationPipeline for spider: {spider.name}")
        logger.info(f"Aggregated {self.items_aggregated} items into {len(self.entity_groups)} entity groups")

        for entity_id, items in self.entity_groups.items():
            items.sort(key=lambda x: x.get("recency_score", 0.0), reverse=True)

            summary = self._generate_entity_summary(entity_id, items)

            if summary:
                logger.info(f"Entity {entity_id}: Generated summary from {len(items)} items")
                logger.debug(f"Summary: {summary[:200]}...")

    def _generate_entity_summary(self, entity_id: str, items: list[dict[str, Any]]) -> str:

        context_parts = []
        for item in items[:10]:
            recency = item.get("recency_score", 0.0)
            title = item.get("title", "")
            content = item.get("content", "")[:200]
            context_parts.append(f"[Recency: {recency:.2f}] {title}: {content}")

        context = "\n".join(context_parts)

        _ = f"""Synthesize the following information about entity '{entity_id}'.
Prioritize facts from entries with higher recency scores (closer to 1.0).

{context}

Summary:"""

        return f"Summary for {entity_id} based on {len(items)} sources (most recent first)"

class MetadataExtractionPipeline:

    BATCH_SIZE = 100
    MAX_KEYWORDS = 10

    def __init__(
        self,
        enabled: bool = True,
        extractor_type: str = "yake",
        batch_size: int = 100,
        max_keywords: int = 10,
    ):
        """Initialize the metadata extraction pipeline.

        Args:
            enabled: Whether pipeline is enabled
            extractor_type: Type of keyword extractor ('yake' or 'spacy')
            batch_size: Number of items to batch before writing
            max_keywords: Maximum keywords to extract per document
        """
        self.enabled = enabled
        self.extractor_type = extractor_type
        self.batch_size = batch_size
        self.max_keywords = max_keywords
        self.batch = []
        self.items_processed = 0

        self.extractor = self._init_extractor(extractor_type)

    def _init_extractor(self, extractor_type: str):
        if extractor_type == "yake":
            try:
                import yake

                return yake.KeywordExtractor(
                    lan="en",
                    n=3,
                    dedupLim=0.9,
                    top=self.max_keywords,
                    features=None,
                )
            except ImportError:
                logger.warning("YAKE not installed, falling back to simple extractor")
                return None
        elif extractor_type == "spacy":
            try:
                import spacy

                return spacy.load("en_core_web_sm")
            except (ImportError, OSError):
                logger.warning("spaCy not available, falling back to simple extractor")
                return None
        else:
            logger.warning(f"Unknown extractor type: {extractor_type}, using simple extractor")
            return None

    @classmethod
    def from_crawler(cls, crawler: "Crawler") -> "MetadataExtractionPipeline":
        enabled = crawler.settings.getbool("METADATA_EXTRACTION_ENABLED", True)
        extractor_type = crawler.settings.get("METADATA_EXTRACTOR_TYPE", "yake")
        batch_size = crawler.settings.getint("METADATA_BATCH_SIZE", 100)
        max_keywords = crawler.settings.getint("METADATA_MAX_KEYWORDS", 10)

        pipeline = cls(
            enabled=enabled,
            extractor_type=extractor_type,
            batch_size=batch_size,
            max_keywords=max_keywords,
        )

        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not self.enabled:
            return item

        adapter = ItemAdapter(item)
        text_content = adapter.get("content") or adapter.get("text") or adapter.get("body")

        if not text_content or not isinstance(text_content, str):
            return item

        metadata = self._extract_metadata(text_content, adapter)

        adapter["extracted_metadata"] = metadata

        record = {
            "url": adapter.get("url"),
            "title": adapter.get("title", ""),
            "keywords": metadata.get("keywords", []),
            "entities": metadata.get("entities", {}),
            "extraction_timestamp": datetime.utcnow().isoformat() + "Z",
            "spider_name": spider.name,
        }

        self.batch.append(record)
        self.items_processed += 1

        if len(self.batch) >= self.batch_size:
            self._save_batch()

        if self.items_processed % 500 == 0:
            logger.info(
                f"[METADATA] Processed {self.items_processed} items, extracted metadata from {len(self.batch)} pending"
            )

        return item

    def _extract_metadata(self, text: str, adapter: ItemAdapter) -> dict[str, Any]:
        metadata = {"keywords": [], "entities": {}}

        if self.extractor:
            if self.extractor_type == "yake":
                keywords = self._extract_keywords_yake(text)
            elif self.extractor_type == "spacy":
                keywords, entities = self._extract_keywords_spacy(text)
                metadata["entities"] = entities
            else:
                keywords = self._extract_keywords_simple(text)
        else:
            keywords = self._extract_keywords_simple(text)

        metadata["keywords"] = keywords

        return metadata

    def _extract_keywords_yake(self, text: str) -> list[str]:
        try:
            keywords_with_scores = self.extractor.extract_keywords(text)
            return [kw for kw, score in keywords_with_scores[: self.max_keywords]]
        except Exception as e:
            logger.warning(f"YAKE extraction failed: {e}")
            return self._extract_keywords_simple(text)

    def _extract_keywords_spacy(self, text: str) -> tuple[list[str], dict[str, list[str]]]:
        try:
            doc = self.extractor(text[:1000000])

            keywords = []
            for chunk in doc.noun_chunks:
                if len(keywords) < self.max_keywords:
                    keywords.append(chunk.text.lower())

            entities = defaultdict(list)
            for ent in doc.ents:
                entities[ent.label_].append(ent.text)

            return keywords, dict(entities)

        except Exception as e:
            logger.warning(f"spaCy extraction failed: {e}")
            return self._extract_keywords_simple(text), {}

    def _extract_keywords_simple(self, text: str) -> list[str]:
        from collections import Counter

        words = re.findall(r"\b[a-z]{4,}\b", text.lower())

        stop_words = {
            "this",
            "that",
            "with",
            "from",
            "have",
            "been",
            "were",
            "said",
            "will",
            "they",
            "their",
            "what",
            "about",
            "which",
            "when",
            "there",
            "than",
            "them",
            "these",
            "would",
            "could",
            "should",
        }

        filtered_words = [w for w in words if w not in stop_words]

        counter = Counter(filtered_words)
        top_keywords = [word for word, count in counter.most_common(self.max_keywords)]

        return top_keywords

    def _save_batch(self):
        if not self.batch:
            return

        batch_size = len(self.batch)

        try:
            from src.common.delta_lake import get_delta_manager

            delta = get_delta_manager()
            delta.write("metadata_queue", self.batch, mode="append")
            logger.info(f"✅ Saved {batch_size} metadata records to metadata_queue")

            self.batch.clear()
        except Exception as e:
            logger.error(f"Failed to save metadata batch: {e}")

    def spider_closed(self, spider: Spider) -> None:
        logger.info(f"[METADATA] Closing MetadataExtractionPipeline for spider: {spider.name}")

        if self.batch:
            self._save_batch()

        logger.info(f"[METADATA] Pipeline stats - Total processed: {self.items_processed}")
