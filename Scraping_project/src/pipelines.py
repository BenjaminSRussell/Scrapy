import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
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
from src.core.timeutil import utc_now_iso
from src.utils.kafka_config import producer_durability_config

logger = logging.getLogger(__name__)

def is_queue_routing_item(item: Any) -> bool:
    """True for Scout's plain-dict queue handoffs (``target_stage``/``target_spider``).

    These are persisted by QueueItemPipeline (#608) and are not content records,
    so content pipelines (schema validation, Kafka) must pass them through.
    """
    return isinstance(item, dict) and bool(item.get("target_stage") or item.get("target_spider"))


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

        adapter["scraped_at_utc"] = utc_now_iso()
        adapter["spider_name"] = spider.name
        adapter["pipeline_version"] = self.PIPELINE_VERSION

        self.items_enriched += 1

        if self.items_enriched % 1000 == 0:
            logger.info(f"Enriched {self.items_enriched} items with metadata")

        return item

try:  # Kafka produce durability (#175, #249)
    from prometheus_client import Counter as _KCounter

    KAFKA_PRODUCE_FAILURES = _KCounter(
        "kafka_produce_failures_total",
        "Kafka messages that could not be delivered, by reason (produce_error|delivery_failed|undelivered_on_close).",
        ["reason"],
    )
    KAFKA_SPILLED = _KCounter(
        "kafka_spilled_messages_total",
        "Undeliverable Kafka messages written to the local spill file instead of being dropped.",
        ["reason"],
    )
except Exception:  # prometheus_client missing or metric already registered
    KAFKA_PRODUCE_FAILURES = KAFKA_SPILLED = None


class KafkaPipeline:
    """Publish items to Kafka without silently losing them (#175, #249).

    * ``produce`` errors (local queue full, transient client errors) are retried
      ``KAFKA_PRODUCE_RETRIES`` times with backoff.
    * A message that still cannot be produced, fails delivery asynchronously, or
      is still undelivered after the close-time flush is appended (fsync'd JSONL)
      to ``KAFKA_SPILL_DIR`` for replay, and counted in
      ``kafka_produce_failures_total`` / ``kafka_spilled_messages_total``.
      Items are only dropped (DropItem) if even the spill write fails.
    * Shutdown: ``close_spider`` flushes for ``KAFKA_CLOSE_FLUSH_TIMEOUT``
      seconds (default 30). Pod ``terminationGracePeriodSeconds`` must exceed
      that flush plus the rest of spider shutdown (Helm default 120s), or
      SIGKILL lands mid-flush and even the spill cannot run.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        producer_config: dict[str, Any] | None = None,
        spill_dir: str | Path = "data/kafka_spill",
        produce_retries: int = 3,
        retry_backoff: float = 0.2,
        close_flush_timeout: float = 30.0,
    ):
        """Initialize the Kafka pipeline.

        Args:
            bootstrap_servers: Comma-separated list of Kafka broker addresses
            topic: Target Kafka topic name
            producer_config: Optional additional producer configuration
            spill_dir: Where undeliverable messages are written (JSONL)
            produce_retries: Attempts per message before spilling
            retry_backoff: Base backoff seconds between produce attempts
            close_flush_timeout: Seconds to flush on spider close
        """
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.producer_config = producer_config or {}
        self.producer: KafkaProducer | None = None
        self.messages_sent = 0
        self.messages_failed = 0
        self.messages_spilled = 0
        self.spill_dir = Path(spill_dir)
        self.produce_retries = max(1, int(produce_retries))
        self.retry_backoff = float(retry_backoff)
        self.close_flush_timeout = float(close_flush_timeout)
        # Messages handed to librdkafka but not yet acknowledged, so anything
        # still pending after the close flush can be spilled (#249).
        self._inflight: dict[int, bytes] = {}
        self._next_id = 0

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
            spill_dir=crawler.settings.get("KAFKA_SPILL_DIR", "data/kafka_spill"),
            produce_retries=crawler.settings.getint("KAFKA_PRODUCE_RETRIES", 3),
            retry_backoff=crawler.settings.getfloat("KAFKA_PRODUCE_RETRY_BACKOFF", 0.2),
            close_flush_timeout=crawler.settings.getfloat("KAFKA_CLOSE_FLUSH_TIMEOUT", 30.0),
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
            "retries": 3,
            "max.in.flight.requests.per.connection": 5,
            # acks=all + idempotence: no loss on leader failover (#174).
            **producer_durability_config(),
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
                remaining = self.producer.flush(timeout=self.close_flush_timeout)
            except Exception as e:
                logger.error(f"Error flushing Kafka producer: {e}")
                remaining = len(self._inflight)
            if remaining > 0 or self._inflight:
                # Undelivered after the flush: spill rather than lose them (#249).
                pending = list(self._inflight.values())
                self._inflight.clear()
                logger.error(
                    f"{remaining} Kafka messages undelivered after {self.close_flush_timeout}s flush; "
                    f"spilling {len(pending)} to {self.spill_dir}"
                )
                if KAFKA_PRODUCE_FAILURES is not None:
                    KAFKA_PRODUCE_FAILURES.labels(reason="undelivered_on_close").inc(max(remaining, len(pending)))
                for value in pending:
                    self._spill(value, "undelivered_on_close")
            logger.info(
                f"Kafka pipeline stats - Sent: {self.messages_sent}, Failed: {self.messages_failed}, "
                f"Spilled: {self.messages_spilled}"
            )

    def _spill(self, value: bytes, reason: str, error: str = "") -> bool:
        """Append one undeliverable message to the spill file (fsync'd)."""
        try:
            self.spill_dir.mkdir(parents=True, exist_ok=True)
            path = self.spill_dir / f"{self.topic}.jsonl"
            record = {
                "topic": self.topic,
                "reason": reason,
                "error": error,
                "spilled_at": datetime.now().isoformat(),
                "value": value.decode("utf-8", errors="replace"),
            }
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except Exception as e:
            logger.critical(f"DATA LOSS: could not spill Kafka message ({reason}): {e}")
            return False
        self.messages_spilled += 1
        if KAFKA_SPILLED is not None:
            KAFKA_SPILLED.labels(reason=reason).inc()
        return True

    def _delivery_callback(self, msg_id: int):
        def _cb(err: Any, msg: Any) -> None:
            value = self._inflight.pop(msg_id, None)
            self.delivery_report(err, msg)
            if err is not None:
                if KAFKA_PRODUCE_FAILURES is not None:
                    KAFKA_PRODUCE_FAILURES.labels(reason="delivery_failed").inc()
                payload = value if value is not None else (msg.value() if msg is not None else None)
                if payload is not None:
                    self._spill(payload, "delivery_failed", str(err))
        return _cb

    def delivery_report(self, err: Any, msg: Any) -> None:
        if err is not None:
            self.messages_failed += 1
            logger.error(f"Message delivery failed: {err}")
        else:
            self.messages_sent += 1
            if self.messages_sent % 1000 == 0:
                logger.info(f"Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}")

    def process_item(self, item: Any, spider: Spider) -> Any:
        if is_queue_routing_item(item):
            return item  # queue handoff, persisted by QueueItemPipeline (#608)
        try:
            item_dict = ItemAdapter(item).asdict()
            value = json.dumps(item_dict, ensure_ascii=False, default=str).encode("utf-8")
        except Exception as e:
            logger.error(f"Error serialising item for Kafka: {e}")
            raise DropItem(f"Failed to serialise item for Kafka: {e}") from e

        if self.producer is None:
            raise DropItem("Kafka producer is not initialized")

        last_error: Exception | None = None
        for attempt in range(1, self.produce_retries + 1):
            msg_id = self._next_id
            self._next_id += 1
            try:
                self._inflight[msg_id] = value
                self.producer.produce(
                    topic=self.topic,
                    value=value,
                    callback=self._delivery_callback(msg_id),
                )
                self.producer.poll(0)
                return item
            except Exception as e:  # BufferError (queue full), KafkaException, ...
                self._inflight.pop(msg_id, None)
                last_error = e
                if attempt < self.produce_retries:
                    logger.warning(f"Kafka produce failed (attempt {attempt}/{self.produce_retries}): {e}")
                    try:
                        # Serve delivery callbacks, which frees local queue space.
                        self.producer.poll(self.retry_backoff * attempt)
                    except Exception:
                        time.sleep(self.retry_backoff * attempt)

        if KAFKA_PRODUCE_FAILURES is not None:
            KAFKA_PRODUCE_FAILURES.labels(reason="produce_error").inc()
        logger.error(f"Kafka produce failed after {self.produce_retries} attempts: {last_error}; spilling")
        if self._spill(value, "produce_error", str(last_error)):
            return item  # durably captured: keep the item flowing (#175)
        raise DropItem(f"Failed to publish item to Kafka and to spill it: {last_error}")

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

        # Copy: later pipelines (Metadata, Recency) mutate the item in place and
        # must not add columns to the queued row before the batch flushes.
        if target_spider == "javascript":
            self.js_queue_batch.append(dict(item))
            self.items_processed += 1

            if len(self.js_queue_batch) >= self.BATCH_SIZE:
                self._save_js_queue_batch()

        elif target_stage == "stage2":
            self.stage2_queue_batch.append(dict(item))
            self.items_processed += 1

            if len(self.stage2_queue_batch) >= self.BATCH_SIZE:
                self._save_stage2_queue_batch()
        else:
            # Content records (dicts without routing metadata) are not queue
            # handoffs; pass them through untouched now that this pipeline is
            # registered for every crawl (#608).
            return item

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
                logger.debug(f"Sampled content from item #{self.items_processed}")

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
                **producer_durability_config(),  # #174
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

        if isinstance(item, OffsiteCandidateItem) or is_queue_routing_item(item):
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
    """Group items by ``entity_id`` and persist one summary row per entity (#790).

    Memory is bounded: each entity keeps only its ``max_items_per_entity``
    most recent items (by ``recency_score``) plus a running total count, and
    at most ``max_entities`` entities are held at once (#201). Beyond that the
    least-recently-updated entities are summarised and spilled to Delta.
    Every ``flush_every_items`` items all buffered groups are flushed, and on
    spider close the rest are written synchronously to the Delta table
    ``output_topic`` (default ``entity_summaries``).

    A flushed entity that keeps receiving items gets another row later, so an
    entity can have several rows per crawl. Each row covers the items seen
    since its previous flush (``source_count``); take the latest ``created_at``
    or sum ``source_count`` downstream.
    """

    DEFAULT_MAX_ITEMS_PER_ENTITY = 10
    DEFAULT_MAX_ENTITIES = 10_000
    DEFAULT_FLUSH_EVERY_ITEMS = 50_000

    def __init__(
        self,
        enabled: bool = True,
        output_topic: str = "entity_summaries",
        max_items_per_entity: int = DEFAULT_MAX_ITEMS_PER_ENTITY,
        persist: bool = True,
        delta: Any = None,
        max_entities: int = DEFAULT_MAX_ENTITIES,
        flush_every_items: int = DEFAULT_FLUSH_EVERY_ITEMS,
    ):
        """Initialize the aggregation pipeline.

        Args:
            enabled: Whether aggregation is enabled
            output_topic: Delta table (and topic name) for entity summaries
            max_items_per_entity: Most-recent items retained per entity
            persist: Write summaries to Delta on spider close
            delta: Optional DeltaHelper-like sink (defaults to get_delta())
            max_entities: Max entity groups held in memory before LRU spill
            flush_every_items: Flush all groups every N items (0 disables)
        """
        self.enabled = enabled
        self.output_topic = output_topic
        self.max_items_per_entity = max(1, int(max_items_per_entity))
        self.persist = persist
        self._delta = delta
        self.max_entities = max(1, int(max_entities))
        self.flush_every_items = max(0, int(flush_every_items))
        # Insertion order doubles as LRU order (touched entities move to the end).
        self.entity_groups: dict[str, list[dict[str, Any]]] = {}
        self.entity_counts: dict[str, int] = {}
        self.items_aggregated = 0
        self.items_since_flush = 0
        self.summaries_written = 0
        self.flushes = 0
        self._spider_name = ""

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "AggregationPipeline":
        enabled = crawler.settings.getbool("AGGREGATION_ENABLED", True)
        output_topic = crawler.settings.get("AGGREGATION_OUTPUT_TOPIC", "entity_summaries")
        max_items = crawler.settings.getint(
            "AGGREGATION_MAX_ITEMS_PER_ENTITY", cls.DEFAULT_MAX_ITEMS_PER_ENTITY
        )
        persist = crawler.settings.getbool("AGGREGATION_PERSIST", True)
        max_entities = crawler.settings.getint("AGGREGATION_MAX_ENTITIES", cls.DEFAULT_MAX_ENTITIES)
        flush_every = crawler.settings.getint(
            "AGGREGATION_FLUSH_EVERY_ITEMS", cls.DEFAULT_FLUSH_EVERY_ITEMS
        )

        pipeline = cls(
            enabled=enabled,
            output_topic=output_topic,
            max_items_per_entity=max_items,
            persist=persist,
            max_entities=max_entities,
            flush_every_items=flush_every,
        )

        crawler.signals.connect(pipeline.close_spider, signal=signals.spider_closed)

        return pipeline

    @staticmethod
    def _recency(item: dict[str, Any]) -> float:
        try:
            return float(item.get("recency_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not self.enabled:
            return item

        if isinstance(item, OffsiteCandidateItem):
            return item

        adapter = ItemAdapter(item)
        entity_id = adapter.get("entity_id")

        if entity_id:
            self._spider_name = str(getattr(spider, "name", "") or "")
            group = self.entity_groups.pop(entity_id, None) or []
            self.entity_groups[entity_id] = group  # move to MRU end
            self.entity_counts[entity_id] = self.entity_counts.pop(entity_id, 0) + 1
            group.append(adapter.asdict())
            self.items_aggregated += 1
            self.items_since_flush += 1
            # Bounded memory: keep only the N most recent items per entity.
            if len(group) > self.max_items_per_entity:
                group.sort(key=self._recency, reverse=True)
                del group[self.max_items_per_entity:]

            if self.flush_every_items and self.items_since_flush >= self.flush_every_items:
                self.flush(list(self.entity_groups), reason="periodic")
            elif len(self.entity_groups) > self.max_entities:
                # Spill the least-recently-updated ~10% so writes are batched.
                n = max(1, len(self.entity_groups) - self.max_entities, self.max_entities // 10)
                self.flush(list(self.entity_groups)[:n], reason="max_entities")

        return item

    def flush(self, entity_ids: list[str], reason: str = "manual") -> int:
        """Summarise ``entity_ids``, persist them, and drop them from memory."""
        if not entity_ids:
            return 0
        rows = self.build_summary_rows(self._spider_name, entity_ids)
        for eid in entity_ids:
            self.entity_groups.pop(eid, None)
            self.entity_counts.pop(eid, None)
        if reason == "periodic" or not self.entity_groups:
            self.items_since_flush = 0
        self.flushes += 1
        written = self._persist(rows)
        logger.info(f"AggregationPipeline flushed {len(entity_ids)} entities ({reason}); {written} rows persisted")
        return written

    def _get_delta(self) -> Any:
        if self._delta is None:
            from src.utils.delta import get_delta

            self._delta = get_delta()
        return self._delta

    def build_summary_rows(self, spider_name: str, entity_ids: list[str] | None = None) -> list[dict[str, Any]]:
        created_at = datetime.now().isoformat()
        rows: list[dict[str, Any]] = []
        ids = list(self.entity_groups) if entity_ids is None else entity_ids
        for entity_id in ids:
            items = self.entity_groups.get(entity_id)
            if not items:
                continue
            items.sort(key=self._recency, reverse=True)
            summary = self._generate_entity_summary(entity_id, items)
            if not summary:
                continue
            urls = [str(i.get("url") or i.get("source_url") or "") for i in items]
            rows.append(
                {
                    "entity_id": str(entity_id),
                    "summary": summary,
                    "source_count": int(self.entity_counts.get(entity_id, len(items))),
                    "top_urls": json.dumps([u for u in urls if u]),
                    "max_recency_score": float(self._recency(items[0])) if items else 0.0,
                    "spider": spider_name,
                    "created_at": created_at,
                }
            )
        return rows

    def close_spider(self, spider: Spider) -> None:
        if not self.enabled:
            return

        logger.info(f"Closing AggregationPipeline for spider: {spider.name}")
        logger.info(f"Aggregated {self.items_aggregated} items into {len(self.entity_groups)} entity groups")

        rows = self.build_summary_rows(str(getattr(spider, "name", "") or ""))
        for row in rows:
            logger.debug(f"Entity {row['entity_id']}: summary from {row['source_count']} items")
        self._persist(rows)

    def _persist(self, rows: list[dict[str, Any]]) -> int:
        """Write summary rows synchronously; never raises. Returns rows written."""
        if not rows or not self.persist:
            return 0
        try:
            ok = self._get_delta().write(self.output_topic, rows, mode="append", async_write=False)
        except Exception as e:  # never fail the crawl / shutdown on persistence
            logger.error(f"Failed to persist {len(rows)} entity summaries: {e}")
            return 0
        if ok is False:
            logger.error(f"Failed to persist {len(rows)} entity summaries to {self.output_topic}")
            return 0
        self.summaries_written += len(rows)
        logger.info(f"Persisted {len(rows)} entity summaries to {self.output_topic}")
        return len(rows)

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
        self.batch: list[dict[str, Any]] = []
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
            "extraction_timestamp": utc_now_iso(),
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
        metadata: dict[str, Any] = {"keywords": [], "entities": {}}

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

            keywords: list[str] = []
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
            import json

            from src.utils.delta import get_delta

            # Parquet can't write a struct column with zero fields, which
            # is exactly what pyarrow infers for "entities" when every
            # record in the batch has {} (the common case: the "simple"
            # extractor never populates it, and most pages have no named
            # entities). JSON-encode it as a string column instead of a
            # nested struct, same pattern EntitySummaryStorage uses for
            # source_references - keeps writes working regardless of
            # whether entities happens to be empty for a whole batch.
            records_to_write = [
                {**record, "entities": json.dumps(record.get("entities", {}))} for record in self.batch
            ]

            delta = get_delta()
            # Synchronous: this can be the final flush from spider_closed(),
            # and an async-queued write has no guarantee of draining before
            # the process exits, which would silently drop the batch.
            delta.write("metadata_queue", records_to_write, mode="append", async_write=False)
            logger.info(f" Saved {batch_size} metadata records to metadata_queue")

            self.batch.clear()
        except Exception as e:
            logger.error(f"Failed to save metadata batch: {e}")

    def spider_closed(self, spider: Spider) -> None:
        logger.info(f"[METADATA] Closing MetadataExtractionPipeline for spider: {spider.name}")

        if self.batch:
            self._save_batch()

        logger.info(f"[METADATA] Pipeline stats - Total processed: {self.items_processed}")
