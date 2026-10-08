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
from src.utils.kafka_config import (
    enforce_idempotent_producer,
    idempotence_required,
    message_key,
    producer_durability_config,
)

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

try:  # Bronze schema outcomes (#302, #227)
    from prometheus_client import Counter as _SCounter

    SCHEMA_DROPS = _SCounter(
        "scrapy_schema_validation_drops_total",
        "Items dropped by SchemaValidationPipeline, by the first failing field.",
        ["spider", "field"],
    )
    MISSING_PUBLICATION_DATE = _SCounter(
        "scrapy_items_missing_publication_date_total",
        "Bronze items accepted without a publication_date (ordering falls back to scraped_at_utc).",
        ["spider"],
    )
except Exception:  # prometheus_client missing or metric already registered
    SCHEMA_DROPS = MISSING_PUBLICATION_DATE = None


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
        message_key_field: str | None = "url_hash",
        require_idempotence: bool | None = None,
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
            message_key_field: Record field used as the Kafka message key (#285);
                empty/None sends unkeyed messages
            require_idempotence: Refuse to start unless the producer is idempotent
                (#464); None reads KAFKA_REQUIRE_IDEMPOTENCE
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
        self.message_key_field = message_key_field or None
        self.require_idempotence = idempotence_required(require_idempotence)
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
            message_key_field=crawler.settings.get("KAFKA_MESSAGE_KEY_FIELD", "url_hash"),
            require_idempotence=crawler.settings.getbool("KAFKA_REQUIRE_IDEMPOTENCE", False),
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
        if self.require_idempotence:
            enforce_idempotent_producer(config)  # #464: fail fast, never run non-idempotent

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
        """Append one undeliverable message to the spill file (fsync'd).

        Every undeliverable message is also dead-lettered (stage=kafka, #162)
        so it shows up in ``python -m src.utils.dead_letter_queue list``.
        """
        self._dead_letter(value, reason, error)
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

    def _dead_letter(self, value: bytes, reason: str, error: str) -> None:
        """Record an undeliverable message in the DLQ; never raises (#162).

        DLQ dir: ``$DLQ_PATH``, else a ``dlq`` sibling of the spill dir
        (data/dlq by default, the same place Stage 2 dead-letters to).
        Disable with ``KAFKA_DLQ_ENABLED=0``.
        """
        if os.getenv("KAFKA_DLQ_ENABLED", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        try:
            dlq = getattr(self, "_dlq", None)
            if dlq is None:
                from src.utils.dead_letter_queue import DeadLetterQueue, default_dlq_path

                dlq = DeadLetterQueue(default_dlq_path(fallback=Path(self.spill_dir).parent / "dlq"))
                self._dlq = dlq
            try:
                item = json.loads(value)
            except (ValueError, UnicodeDecodeError):
                item = {"raw": value.decode("utf-8", errors="replace")}
            if not isinstance(item, dict):
                item = {"value": item}
            dlq.add(
                item,
                RuntimeError(f"Kafka {reason}: {error}" if error else f"Kafka {reason}"),
                stage="kafka",
                context={
                    "topic": self.topic,
                    "reason": reason,
                    "spill_file": str(Path(self.spill_dir) / f"{self.topic}.jsonl"),
                },
            )
        except Exception as e:
            logger.error(f"Could not dead-letter Kafka message ({reason}): {e}")

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

        # Same URL -> same partition -> ordered, dedupable by consumers (#285).
        key = message_key(item_dict, self.message_key_field)

        last_error: Exception | None = None
        for attempt in range(1, self.produce_retries + 1):
            msg_id = self._next_id
            self._next_id += 1
            try:
                self._inflight[msg_id] = value
                self.producer.produce(
                    topic=self.topic,
                    key=key,
                    value=value,
                    callback=self._delivery_callback(msg_id),
                )
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
                continue
            # Enqueued: from here the delivery callback or the close-time flush/spill
            # owns the message. A failing poll must not re-produce it (duplicate, #660).
            try:
                self.producer.poll(0)
            except Exception as e:
                logger.warning(f"Kafka poll after produce failed: {e}; message stays in flight")
            return item

        if KAFKA_PRODUCE_FAILURES is not None:
            KAFKA_PRODUCE_FAILURES.labels(reason="produce_error").inc()
        logger.error(f"Kafka produce failed after {self.produce_retries} attempts: {last_error}; spilling")
        if self._spill(value, "produce_error", str(last_error)):
            return item  # durably captured: keep the item flowing (#175)
        raise DropItem(f"Failed to publish item to Kafka and to spill it: {last_error}")

try:  # buffered Delta batch flushes (#424, #425)
    from prometheus_client import Counter as _BCounter

    PIPELINE_BATCH_FLUSHES = _BCounter(
        "pipeline_batch_flushes_total",
        "Pipeline Delta batch flushes by table, trigger (count|bytes|age|timer|close) and outcome (ok|failed).",
        ["table", "trigger", "outcome"],
    )
    PIPELINE_BATCH_ROWS_UNWRITTEN = _BCounter(
        "pipeline_batch_rows_unwritten_total",
        "Rows in pipeline Delta batches the lake did not accept (spilled by the manager or failed).",
        ["table"],
    )
except Exception:  # prometheus_client missing or metric already registered
    PIPELINE_BATCH_FLUSHES = PIPELINE_BATCH_ROWS_UNWRITTEN = None


class BufferedDeltaBatch:
    """In-memory Delta batch with bounded size and age (#424, #425).

    A flush happens when the batch reaches ``max_rows`` rows, ``max_bytes``
    (approximate JSON size), or ``max_age`` seconds since the oldest unflushed
    row. Age is checked both on every ``add`` and by ``flush_if_due`` (driven
    by a reactor timer in the pipelines), so an idle crawl still writes its
    tail instead of holding it until ``spider_closed``. A hard kill therefore
    loses at most ``max_age`` seconds of rows.

    The batch is always cleared after a flush attempt: ``DeltaHelper.write``
    never raises, and ``False`` means the manager spilled the rows to
    ``_write_spill/`` (#167), so re-sending them would duplicate data and
    keeping them would grow memory without bound. Those rows are counted in
    ``pipeline_batch_rows_unwritten_total`` and logged instead of the
    misleading "Saved N" message.
    """

    def __init__(
        self,
        delta: Any,
        table: str,
        max_rows: int = 100,
        max_bytes: int = 4 * 1024 * 1024,
        max_age: float = 30.0,
        clock: Any = time.monotonic,
    ):
        self.delta = delta
        self.table = table
        self.max_rows = max(1, int(max_rows))
        self.max_bytes = max(0, int(max_bytes))
        self.max_age = max(0.0, float(max_age))
        self.clock = clock
        self.rows: list[dict[str, Any]] = []
        self.bytes = 0
        self.oldest: float | None = None
        self.rows_written = 0
        self.rows_unwritten = 0
        self.peak_rows = 0

    def __len__(self) -> int:
        return len(self.rows)

    def __bool__(self) -> bool:
        return bool(self.rows)

    def __iter__(self):
        return iter(self.rows)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return self.rows == other
        return NotImplemented

    __hash__ = None

    def clear(self) -> None:
        """Discard buffered rows without writing them."""
        self.rows, self.bytes, self.oldest = [], 0, None

    @staticmethod
    def _size(row: dict[str, Any]) -> int:
        try:
            return len(json.dumps(row, default=str))
        except Exception:
            return len(repr(row))

    def add(self, row: dict[str, Any]) -> bool:
        """Buffer one row; flush if a bound is hit. Returns True if it flushed."""
        if not self.rows:
            self.oldest = self.clock()
        self.rows.append(row)
        self.bytes += self._size(row)
        self.peak_rows = max(self.peak_rows, len(self.rows))
        if len(self.rows) >= self.max_rows:
            return self.flush("count")
        if self.max_bytes and self.bytes >= self.max_bytes:
            return self.flush("bytes")
        if self._aged():
            return self.flush("age")
        return False

    def _aged(self) -> bool:
        return bool(self.rows) and self.max_age > 0 and self.oldest is not None and (
            self.clock() - self.oldest >= self.max_age
        )

    def flush_if_due(self) -> bool:
        """Timer hook: flush when the oldest row is older than ``max_age``."""
        return self.flush("timer") if self._aged() else False

    def flush(self, trigger: str = "manual") -> bool:
        if not self.rows:
            return False
        batch, self.rows, self.bytes, self.oldest = self.rows, [], 0, None
        try:
            ok = self.delta.write(self.table, batch, mode="append") is not False
        except Exception as e:  # defensive: DeltaHelper normally returns False instead
            logger.error(f"Failed to save {len(batch)} rows to {self.table}: {e}")
            ok = False
        if ok:
            self.rows_written += len(batch)
            logger.info(f" Saved {len(batch)} rows to {self.table} ({trigger})")
        else:
            self.rows_unwritten += len(batch)
            logger.error(
                f"Delta did not accept {len(batch)} rows for {self.table} ({trigger}); "
                "they were spilled by the lakehouse manager or failed (see lakehouse logs)"
            )
            if PIPELINE_BATCH_ROWS_UNWRITTEN is not None:
                PIPELINE_BATCH_ROWS_UNWRITTEN.labels(table=self.table).inc(len(batch))
        if PIPELINE_BATCH_FLUSHES is not None:
            PIPELINE_BATCH_FLUSHES.labels(table=self.table, trigger=trigger, outcome="ok" if ok else "failed").inc()
        return ok


def _batch_settings(crawler: Crawler | None, prefix: str, default_rows: int) -> dict[str, Any]:
    settings = getattr(crawler, "settings", None)
    if settings is None:
        return {"max_rows": default_rows}
    return {
        "max_rows": settings.getint(f"{prefix}_BATCH_SIZE", default_rows),
        "max_bytes": settings.getint(f"{prefix}_BATCH_MAX_BYTES", 4 * 1024 * 1024),
        "max_age": settings.getfloat(f"{prefix}_FLUSH_INTERVAL", 30.0),
    }


class _TimedFlushMixin:
    """Start a reactor LoopingCall that flushes aged batches (#425)."""

    _flush_loop: Any = None
    _delta: Any = None

    @property
    def delta(self) -> Any:
        return self._delta

    @delta.setter
    def delta(self, value: Any) -> None:
        # Tests and callers swap the Delta helper after construction; keep the
        # batches writing to the same one.
        self._delta = value
        for batch in self.__dict__.get("_batch_list", ()):
            batch.delta = value

    def _batches(self) -> list[BufferedDeltaBatch]:
        return list(self.__dict__.get("_batch_list", ()))

    def _flush_due(self) -> None:
        for batch in self._batches():
            try:
                batch.flush_if_due()
            except Exception as e:  # never kill the timer
                logger.error(f"Timed flush of {batch.table} failed: {e}")

    def _start_flush_timer(self, spider: Spider | None = None) -> None:
        interval = min((b.max_age for b in self._batches() if b.max_age > 0), default=0)
        if not interval or self._flush_loop is not None:
            return
        try:
            from twisted.internet import task

            loop = task.LoopingCall(self._flush_due)
            loop.start(max(0.05, interval / 2), now=False)
            self._flush_loop = loop
        except Exception as e:
            logger.warning(f"Timed batch flush disabled: {e}")

    def _stop_flush_timer(self) -> None:
        loop, self._flush_loop = self._flush_loop, None
        if loop is not None and loop.running:
            loop.stop()


class QueueItemPipeline(_TimedFlushMixin):
    """Batch queue hand-offs into ``js_spider_queue`` / ``stage2_queue``.

    Batches are bounded by rows, bytes and age and flushed by a reactor timer
    as well as on ``spider_closed`` (#425): settings ``QUEUE_BATCH_SIZE``,
    ``QUEUE_BATCH_MAX_BYTES``, ``QUEUE_FLUSH_INTERVAL`` (seconds).
    """

    BATCH_SIZE = 100

    def __init__(self, batch_settings: dict[str, Any] | None = None):
        from src.utils.delta import get_delta

        self.delta = get_delta()
        opts = batch_settings or {"max_rows": self.BATCH_SIZE}
        self.js_queue_batch = BufferedDeltaBatch(self.delta, "js_spider_queue", **opts)
        self.stage2_queue_batch = BufferedDeltaBatch(self.delta, "stage2_queue", **opts)
        self._batch_list = [self.js_queue_batch, self.stage2_queue_batch]
        self.items_processed = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "QueueItemPipeline":
        pipeline = cls(_batch_settings(crawler, "QUEUE", cls.BATCH_SIZE))

        crawler.signals.connect(pipeline.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)

        return pipeline

    def spider_opened(self, spider: Spider) -> None:
        self._start_flush_timer(spider)

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not isinstance(item, dict):
            return item

        target_spider = item.get("target_spider")
        target_stage = item.get("target_stage")

        if target_spider == "javascript" or target_stage == "stage2":
            from src.utils.ssrf import count_blocked, ssrf_block_reason

            reason = ssrf_block_reason(str(item.get("url") or ""))
            if reason is not None:  # never queue an SSRF-like target (#682)
                self.ssrf_dropped = getattr(self, "ssrf_dropped", 0) + 1
                count_blocked("queue", reason)
                logger.warning(f"[QUEUE] Not queueing {item.get('url')!r}: ssrf_blocked:{reason}")
                return item

        # Copy: later pipelines (Metadata, Recency) mutate the item in place and
        # must not add columns to the queued row before the batch flushes.
        if target_spider == "javascript":
            self.js_queue_batch.add(dict(item))
            self.items_processed += 1
        elif target_stage == "stage2":
            self.stage2_queue_batch.add(dict(item))
            self.items_processed += 1
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
        self.js_queue_batch.flush("manual")

    def _save_stage2_queue_batch(self):
        self.stage2_queue_batch.flush("manual")

    def spider_closed(self, spider: Spider) -> None:
        logger.info(f"[QUEUE] Closing QueueItemPipeline for spider: {spider.name}")
        self._stop_flush_timer()

        self.js_queue_batch.flush("close")
        self.stage2_queue_batch.flush("close")

        logger.info(f"[QUEUE] Pipeline stats - Total processed: {self.items_processed}")

class OffsiteCandidatePipeline(_TimedFlushMixin):
    """Batch offsite candidates into ``stage1_offsite_candidates``.

    Memory is bounded (#424): the batch flushes at ``OFFSITE_BATCH_SIZE`` rows,
    ``OFFSITE_BATCH_MAX_BYTES`` bytes or ``OFFSITE_FLUSH_INTERVAL`` seconds,
    and is cleared after every attempt, so a noisy page or a failing lake
    cannot make it grow until close.
    """

    BATCH_SIZE = 100

    def __init__(self, batch_settings: dict[str, Any] | None = None):
        from src.utils.delta import get_delta

        self.delta = get_delta()
        self.batch = BufferedDeltaBatch(
            self.delta, "stage1_offsite_candidates", **(batch_settings or {"max_rows": self.BATCH_SIZE})
        )
        self._batch_list = [self.batch]
        self.items_processed = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "OffsiteCandidatePipeline":
        pipeline = cls(_batch_settings(crawler, "OFFSITE", cls.BATCH_SIZE))

        crawler.signals.connect(pipeline.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)

        return pipeline

    def spider_opened(self, spider: Spider) -> None:
        self._start_flush_timer(spider)

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not isinstance(item, OffsiteCandidateItem):
            return item

        missing = item.missing_required()
        if missing:
            # A row without its source/target/timestamp cannot be reviewed (#247).
            raise DropItem(f"OffsiteCandidateItem missing required field(s): {', '.join(missing)}")

        adapter = ItemAdapter(item)
        before = self.batch.rows_written
        self.batch.add(adapter.asdict())
        self.items_processed += 1
        self._count_saved(spider, self.batch.rows_written - before)

        if self.items_processed % 500 == 0:
            logger.info(f"Processed {self.items_processed} offsite candidates")

        return item

    @staticmethod
    def _count_saved(spider: Spider | None, n: int) -> None:
        if n <= 0:
            return
        try:
            from src.scrapy_prometheus import OFFSITE_CANDIDATES_SAVED

            if OFFSITE_CANDIDATES_SAVED:
                OFFSITE_CANDIDATES_SAVED.labels(spider=getattr(spider, "name", None) or "scout").inc(n)
        except ImportError:
            pass

    def _save_batch(self, spider: Spider | None = None, trigger: str = "manual"):
        before = self.batch.rows_written
        self.batch.flush(trigger)
        self._count_saved(spider, self.batch.rows_written - before)

    def _flush_due(self) -> None:
        before = self.batch.rows_written
        super()._flush_due()
        self._count_saved(None, self.batch.rows_written - before)

    def spider_closed(self, spider: Spider) -> None:
        logger.info(f"Closing OffsiteCandidatePipeline for spider: {spider.name}")
        self._stop_flush_timer()

        self._save_batch(spider, "close")

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
            # Optional telemetry (#462): a sampling/export failure must never
            # drop the item or fail the crawl.
            try:
                self._sample(item, spider)
            except Exception as e:
                self._skip(spider, "sample_error", e)

        return item

    def _sample(self, item: Any, spider: Spider) -> None:
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

    def _skip(self, spider: Spider, reason: str, error: BaseException | None = None) -> None:
        """Record a skipped summary export (``summary_skipped`` stat + metric)."""
        stats = getattr(getattr(spider, "crawler", None), "stats", None)
        if stats is not None:
            stats.inc_value("summary_skipped")
            stats.inc_value(f"summary_skipped/{reason}")
        try:
            from src.scrapy_prometheus import CRAWLER_SUMMARY_SKIPPED

            if CRAWLER_SUMMARY_SKIPPED is not None:
                CRAWLER_SUMMARY_SKIPPED.labels(spider=spider.name, reason=reason).inc()
        except Exception:  # metrics are best effort here
            pass
        if error is None:
            logger.debug(f"GrafanaSummaryPipeline skipped summary export ({reason})")
        else:
            logger.warning(f"GrafanaSummaryPipeline skipped summary export ({reason}): {error}")
        self.sampled_content = []

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
        except Exception as e:  # missing/broken optional metrics deps (#462)
            self._skip(spider, "deps_unavailable", e)
            return

        if CRAWLER_CONTENT_SUMMARY is None:
            self._skip(spider, "metrics_disabled")
            return

        try:
            # Note: Prometheus Gauge doesn't accept string values directly
            CRAWLER_CONTENT_SUMMARY.labels(spider=spider.name).set(len(self.sampled_content))
            logger.info(f" Content Summary ({len(self.sampled_content)} samples): {summary[:200]}...")
        except Exception as e:
            self._skip(spider, "export_error", e)
            return

        self.sampled_content = []

    def spider_closed(self, spider: Spider) -> None:
        logger.info(f"Closing GrafanaSummaryPipeline for spider: {spider.name}")

        if self.sampled_content:
            try:
                self._generate_and_export_summary(spider)
            except Exception as e:  # never fail the spider close path (#462)
                self._skip(spider, "close_error", e)

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
            item_dict = self._stamp_bronze_fields(item_dict, spider)

            validated_record = BaseRecordSchema(**item_dict)
            if validated_record.publication_date is None and MISSING_PUBLICATION_DATE is not None:
                MISSING_PUBLICATION_DATE.labels(spider=validated_record.spider_name).inc()

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
            if SCHEMA_DROPS is not None:
                errs = e.errors()
                field = ".".join(str(p) for p in errs[0]["loc"]) if errs and errs[0].get("loc") else "model"
                SCHEMA_DROPS.labels(spider=getattr(spider, "name", "unknown"), field=field).inc()

            self._publish_validation_failure(item_dict, e, spider)

            raise DropItem(f"Schema validation failed for {item_dict.get('url', 'unknown')}: {e}") from e

    @staticmethod
    def _stamp_bronze_fields(item_dict: dict[str, Any], spider: Spider) -> dict[str, Any]:
        """Fill the bronze-required provenance fields (#227) before validation.

        MetadataPipeline (later in ITEM_PIPELINES) stamps the same fields, so
        validating here without them dropped every item that lacked them.
        """
        if not item_dict.get("scraped_at_utc"):
            item_dict["scraped_at_utc"] = utc_now_iso()
        if not item_dict.get("spider_name"):
            name = getattr(spider, "name", None)
            if isinstance(name, str) and name:
                item_dict["spider_name"] = name
        return item_dict

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
