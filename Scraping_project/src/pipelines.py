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
        from src.utils.delta import get_delta

        self.delta = get_delta()
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
            logger.info(f" Saved {batch_size} items to js_spider_queue")
            self.js_queue_batch.clear()
        except Exception as e:
            logger.error(f"Failed to save JS queue batch: {e}")

    def _save_stage2_queue_batch(self):
        if not self.stage2_queue_batch:
            return

        batch_size = len(self.stage2_queue_batch)

        try:
            self.delta.write("stage2_queue", self.stage2_queue_batch, mode="append")
            logger.info(f" Saved {batch_size} items to stage2_queue")
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


from src.pipelines_schema_grafana import (  # noqa: E402
    OffsiteCandidatePipeline,
    GrafanaSummaryPipeline,
    SchemaValidationPipeline,
)
from src.pipelines_scoring import (  # noqa: E402
    RecencyScoringPipeline,
    AggregationPipeline,
    MetadataExtractionPipeline,
)

__all__ = [
    "is_queue_routing_item",
    "DataValidationPipeline",
    "DataCleansingPipeline",
    "MetadataPipeline",
    "KafkaPipeline",
    "QueueItemPipeline",
    "OffsiteCandidatePipeline",
    "GrafanaSummaryPipeline",
    "SchemaValidationPipeline",
    "RecencyScoringPipeline",
    "AggregationPipeline",
    "MetadataExtractionPipeline",
]
