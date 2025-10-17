"""Scrapy pipelines for the scraping project.

This module contains custom pipeline implementations for processing scraped items.
"""

import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from confluent_kafka import Producer as KafkaProducer
else:  # pragma: no cover - typing only
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
    """Pipeline for validating scraped items before further processing.

    This pipeline ensures data quality by validating critical fields and dropping
    invalid items early in the pipeline chain. This prevents bad data from ever
    reaching Kafka or Delta Lake.

    Validation rules:
    - All items must have a 'url' field
    - Text content fields must not be empty or whitespace-only
    - Numeric fields (if present) must be valid numbers
    - Required fields are configurable via settings
    """

    def __init__(self, required_fields: list[str] | None = None):
        """Initialize the validation pipeline.

        Args:
            required_fields: List of field names that must be present in every item
        """
        self.required_fields = required_fields or ["url"]
        self.items_validated = 0
        self.items_dropped = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "DataValidationPipeline":
        """Factory method to create pipeline instance from crawler settings.

        Args:
            crawler: Scrapy crawler instance with settings

        Returns:
            Configured DataValidationPipeline instance
        """
        required_fields = crawler.settings.getlist("VALIDATION_REQUIRED_FIELDS", ["url"])
        return cls(required_fields=required_fields)

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Validate item and raise DropItem if validation fails.

        Args:
            item: The scraped item to validate
            spider: The spider that yielded the item

        Returns:
            The validated item

        Raises:
            DropItem: If item fails validation
        """
        adapter = ItemAdapter(item)

        # Conditional validation: OffsiteCandidateItem uses 'external_url' instead of 'url'
        required_fields = self.required_fields
        if isinstance(item, OffsiteCandidateItem):
            required_fields = ["external_url"]

        # Check required fields
        for field in required_fields:
            if field not in adapter:
                self.items_dropped += 1
                raise DropItem(
                    f"Missing required field '{field}' in item from spider '{spider.name}'. Item: {dict(adapter)}"
                )

            value = adapter.get(field)

            # Check for empty or whitespace-only strings
            if isinstance(value, str) and not value.strip():
                self.items_dropped += 1
                raise DropItem(
                    f"Required field '{field}' is empty or whitespace in item from spider '{spider.name}'. "
                    f"Item: {dict(adapter)}"
                )

            # Check for None values
            if value is None:
                self.items_dropped += 1
                raise DropItem(
                    f"Required field '{field}' is None in item from spider '{spider.name}'. Item: {dict(adapter)}"
                )

        self.items_validated += 1

        # Log validation stats every 1000 items
        if self.items_validated % 1000 == 0:
            logger.info(f"Validation stats - Validated: {self.items_validated}, Dropped: {self.items_dropped}")

        return item


class DataCleansingPipeline:
    """Pipeline for cleansing and normalizing scraped data.

    This pipeline performs data normalization to ensure consistency:
    - Strips leading/trailing whitespace from all string fields
    - Converts currency strings to float values
    - Standardizes categorical data (e.g., lowercase categories)
    - Normalizes URLs and domains
    """

    # Pattern for extracting numeric values from currency strings
    CURRENCY_PATTERN = re.compile(r"[\$£€¥]?\s*([0-9,]+\.?[0-9]*)")

    def __init__(self):
        """Initialize the cleansing pipeline."""
        self.items_cleansed = 0

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Cleanse and normalize item data.

        Args:
            item: The scraped item to cleanse
            spider: The spider that yielded the item

        Returns:
            The cleansed item
        """
        adapter = ItemAdapter(item)

        # Process each field
        for field_name in adapter.field_names():
            value = adapter.get(field_name)

            # Skip None values
            if value is None:
                continue

            # Strip whitespace from strings
            if isinstance(value, str):
                cleaned = value.strip()
                normalized_value: str | float = cleaned

                # Normalize common fields
                if field_name in ("category", "type", "status"):
                    normalized_value = cleaned.lower()

                # Convert currency strings to float
                if field_name in ("price", "cost", "amount"):
                    normalized_value = self._parse_currency(cleaned)

                adapter[field_name] = normalized_value

            # Normalize lists (strip strings in lists)
            elif isinstance(value, list):
                adapter[field_name] = [item.strip() if isinstance(item, str) else item for item in value]

        self.items_cleansed += 1

        if self.items_cleansed % 1000 == 0:
            logger.info(f"Cleansed {self.items_cleansed} items")

        return item

    def _parse_currency(self, value: str) -> float | str:
        """Parse currency string to float.

        Args:
            value: Currency string (e.g., '$19.99', '1,234.56')

        Returns:
            Parsed float value, or original string if parsing fails
        """
        match = self.CURRENCY_PATTERN.search(value)
        if match:
            try:
                # Remove commas and convert to float
                return float(match.group(1).replace(",", ""))
            except ValueError:
                logger.warning(f"Failed to parse currency value: {value}")
                return value
        return value


class MetadataPipeline:
    """Pipeline for enriching items with operational metadata.

    This pipeline adds critical metadata for tracking and auditing:
    - scraped_at_utc: UTC timestamp when item was scraped
    - spider_name: Name of the spider that scraped the item
    - pipeline_version: Version of the pipeline processing the item
    """

    PIPELINE_VERSION = "1.0.0"

    def __init__(self):
        """Initialize the metadata pipeline."""
        self.items_enriched = 0

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Enrich item with metadata.

        Args:
            item: The scraped item to enrich
            spider: The spider that yielded the item

        Returns:
            The enriched item
        """
        adapter = ItemAdapter(item)

        # Add metadata fields
        adapter["scraped_at_utc"] = datetime.utcnow().isoformat() + "Z"
        adapter["spider_name"] = spider.name
        adapter["pipeline_version"] = self.PIPELINE_VERSION

        self.items_enriched += 1

        if self.items_enriched % 1000 == 0:
            logger.info(f"Enriched {self.items_enriched} items with metadata")

        return item


class KafkaPipeline:
    """High-performance Kafka pipeline for streaming scraped items.

    This pipeline is responsible ONLY for serialization and publishing to Kafka.
    All validation, cleansing, and enrichment should be done by earlier pipelines.

    This pipeline uses the confluent-kafka library (librdkafka wrapper) to provide
    enterprise-grade reliability and performance for publishing scraped items to Kafka.

    Features:
    - Asynchronous message delivery with configurable batching
    - Delivery report callbacks for monitoring and error handling
    - Graceful shutdown with message flushing to prevent data loss
    - Secure credential loading from environment variables
    - Automatic JSON serialization with UTF-8 encoding

    Configuration (in settings.py):
    - KAFKA_BOOTSTRAP_SERVERS: Comma-separated list of broker host:port pairs
    - KAFKA_TOPIC: Target topic name for scraped items
    - KAFKA_PRODUCER_CONFIG: Optional dict of additional producer configuration

    Security:
    - KAFKA_SASL_USERNAME: Load from environment (e.g., os.getenv('KAFKA_SASL_USERNAME'))
    - KAFKA_SASL_PASSWORD: Load from environment (e.g., os.getenv('KAFKA_SASL_PASSWORD'))
    - KAFKA_SECURITY_PROTOCOL: e.g., 'SASL_SSL' (load from environment)
    - KAFKA_SASL_MECHANISM: e.g., 'PLAIN', 'SCRAM-SHA-256' (load from environment)
    """

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
        """Factory method to create pipeline instance from crawler settings.

        This is the standard Scrapy pattern for accessing settings and signals.

        Args:
            crawler: Scrapy crawler instance with settings and signals

        Returns:
            Configured KafkaPipeline instance

        Raises:
            NotConfigured: If required settings are missing
        """
        # Check if Kafka is available
        if not KAFKA_AVAILABLE:
            logger.warning("Kafka pipeline disabled - confluent_kafka not installed")
            raise NotConfigured("confluent_kafka library not available")

        # Load required settings
        bootstrap_servers = crawler.settings.get("KAFKA_BOOTSTRAP_SERVERS")
        if not bootstrap_servers:
            raise NotConfigured("KAFKA_BOOTSTRAP_SERVERS setting is required")

        topic = crawler.settings.get("KAFKA_TOPIC")
        if not topic:
            raise NotConfigured("KAFKA_TOPIC setting is required")

        # Load optional producer config
        producer_config = crawler.settings.get("KAFKA_PRODUCER_CONFIG", {})

        # Create pipeline instance
        pipeline = cls(
            bootstrap_servers=bootstrap_servers,
            topic=topic,
            producer_config=producer_config,
        )

        # Connect lifecycle methods to Scrapy signals for robust lifecycle management
        crawler.signals.connect(pipeline.open_spider, signal=signals.spider_opened)
        crawler.signals.connect(pipeline.close_spider, signal=signals.spider_closed)

        return pipeline

    def open_spider(self, spider: Spider) -> None:
        """Initialize Kafka producer when spider opens.

        This method is called when the spider begins crawling. It establishes
        the connection to the Kafka cluster.

        Args:
            spider: The spider that was opened
        """
        logger.info(f"Opening Kafka pipeline for spider: {spider.name}")

        # Build producer configuration
        config = {
            "bootstrap.servers": self.bootstrap_servers,
            # Optimize for throughput and reliability
            "linger.ms": 10,  # Small batching delay for better throughput
            "batch.size": 16384,  # 16KB batch size
            "compression.type": "snappy",  # Fast compression
            "acks": 1,  # Wait for leader acknowledgment
            "retries": 3,  # Retry failed sends
            "max.in.flight.requests.per.connection": 5,
        }

        # Load security settings from environment variables (never hardcode credentials!)
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

        # Merge with any additional config from settings
        config.update(self.producer_config)

        # Initialize producer
        try:
            self.producer = Producer(config)
            logger.info(f"Kafka producer initialized: {self.bootstrap_servers}")
        except Exception as e:
            logger.error(f"Failed to initialize Kafka producer: {e}")
            raise

    def close_spider(self, spider: Spider) -> None:
        """Flush and close Kafka producer when spider closes.

        CRITICAL: This method calls producer.flush() to ensure all buffered messages
        are delivered before the spider shuts down. Without this, messages could be lost.

        Args:
            spider: The spider that was closed
        """
        logger.info(f"Closing Kafka pipeline for spider: {spider.name}")

        if self.producer:
            try:
                # Block until all messages are delivered or timeout (30 seconds)
                remaining = self.producer.flush(timeout=30.0)
                if remaining > 0:
                    logger.warning(f"{remaining} messages were not delivered before timeout")

                logger.info(f"Kafka pipeline stats - Sent: {self.messages_sent}, Failed: {self.messages_failed}")
            except Exception as e:
                logger.error(f"Error flushing Kafka producer: {e}")

    def delivery_report(self, err: Any, msg: Any) -> None:
        """Callback for Kafka message delivery reports.

        This callback is invoked by the producer for each message to report
        delivery success or failure. Essential for monitoring pipeline health.

        Args:
            err: Error object if delivery failed, None if successful
            msg: Message object with metadata
        """
        if err is not None:
            self.messages_failed += 1
            logger.error(f"Message delivery failed: {err}")
        else:
            self.messages_sent += 1
            if self.messages_sent % 1000 == 0:  # Log every 1000 messages
                logger.info(f"Message delivered to {msg.topic()} [{msg.partition()}] at offset {msg.offset()}")

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Serialize and publish item to Kafka.

        This method is called by Scrapy for every item yielded by the spider.
        It serializes the fully processed item to JSON and publishes it to Kafka.

        Note: This pipeline assumes the item has already been validated, cleansed,
        and enriched by earlier pipelines in the chain.

        Args:
            item: The fully processed scraped item
            spider: The spider that yielded the item

        Returns:
            The original item (to allow subsequent pipelines to process it)
        """
        try:
            # Convert item to dictionary using ItemAdapter (works with dicts, scrapy Items, etc.)
            item_dict = ItemAdapter(item).asdict()

            # Serialize to JSON
            message_value = json.dumps(item_dict, ensure_ascii=False, default=str)

            # Publish to Kafka asynchronously
            # The delivery_report callback will be invoked when delivery completes
            if self.producer is None:
                raise RuntimeError("Kafka producer is not initialized")

            self.producer.produce(
                topic=self.topic,
                value=message_value.encode("utf-8"),
                callback=self.delivery_report,
            )

            # Trigger delivery report callbacks (non-blocking)
            # This processes any pending delivery reports without blocking
            self.producer.poll(0)

        except Exception as e:
            logger.error(f"Error processing item for Kafka: {e}")
            raise DropItem(f"Failed to publish item to Kafka: {e}")

        return item


class QueueItemPipeline:
    """Pipeline for routing queue items to appropriate Delta Lake tables.

    REFACTORED: Handles queue items from ScoutSpider dual-queueing strategy.
    Routes items based on target_spider or target_stage fields:
    - target_spider='javascript' → js_spider_queue
    - target_stage='stage2' → stage2_queue

    Features:
    - Batch processing for efficient Delta Lake writes
    - Automatic routing based on item metadata
    - Graceful shutdown with data flushing
    """

    BATCH_SIZE = 100  # Number of items to batch before writing

    def __init__(self):
        """Initialize the queue item pipeline."""
        from src.common.delta_lake import get_delta_manager

        self.delta = get_delta_manager()
        self.js_queue_batch = []
        self.stage2_queue_batch = []
        self.items_processed = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "QueueItemPipeline":
        """Factory method to create pipeline instance from crawler.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            Configured QueueItemPipeline instance
        """
        pipeline = cls()

        # Connect lifecycle methods to Scrapy signals
        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Process queue items and route to appropriate table.

        Args:
            item: The scraped item (dict with target_spider or target_stage)
            spider: The spider that yielded the item

        Returns:
            The original item (to allow subsequent pipelines to process it)
        """
        # Only process dict items with queue routing metadata
        if not isinstance(item, dict):
            return item

        # Check if item has routing metadata
        target_spider = item.get("target_spider")
        target_stage = item.get("target_stage")

        # Route to appropriate queue
        if target_spider == "javascript":
            self.js_queue_batch.append(item)
            self.items_processed += 1

            # Save batch if it reaches BATCH_SIZE
            if len(self.js_queue_batch) >= self.BATCH_SIZE:
                self._save_js_queue_batch()

        elif target_stage == "stage2":
            self.stage2_queue_batch.append(item)
            self.items_processed += 1

            # Save batch if it reaches BATCH_SIZE
            if len(self.stage2_queue_batch) >= self.BATCH_SIZE:
                self._save_stage2_queue_batch()
        else:
            logger.warning(f"QueueItemPipeline: Received a dict item with no routing metadata: {item}")

        # Log progress every 500 items
        if self.items_processed % 500 == 0:
            logger.info(
                f"[QUEUE] Processed {self.items_processed} queue items "
                f"(JS: {len(self.js_queue_batch)}, Stage2: {len(self.stage2_queue_batch)})"
            )

        return item

    def _save_js_queue_batch(self):
        """Save current JS queue batch to Delta Lake."""
        if not self.js_queue_batch:
            return

        batch_size = len(self.js_queue_batch)

        try:
            self.delta.write("js_spider_queue", self.js_queue_batch, mode="append")
            logger.info(f"✅ Saved {batch_size} items to js_spider_queue")
            self.js_queue_batch.clear()  # Clear batch on success
        except Exception as e:
            logger.error(f"Failed to save JS queue batch: {e}")

    def _save_stage2_queue_batch(self):
        """Save current Stage 2 queue batch to Delta Lake."""
        if not self.stage2_queue_batch:
            return

        batch_size = len(self.stage2_queue_batch)

        try:
            self.delta.write("stage2_queue", self.stage2_queue_batch, mode="append")
            logger.info(f"✅ Saved {batch_size} items to stage2_queue")
            self.stage2_queue_batch.clear()  # Clear batch on success
        except Exception as e:
            logger.error(f"Failed to save Stage 2 queue batch: {e}")

    def spider_closed(self, spider: Spider) -> None:
        """Flush remaining batches when spider closes.

        Args:
            spider: The spider that was closed
        """
        logger.info(f"[QUEUE] Closing QueueItemPipeline for spider: {spider.name}")

        # Save any remaining items in both batches
        if self.js_queue_batch:
            self._save_js_queue_batch()

        if self.stage2_queue_batch:
            self._save_stage2_queue_batch()

        logger.info(f"[QUEUE] Pipeline stats - Total processed: {self.items_processed}")


class OffsiteCandidatePipeline:
    """Pipeline for processing external URLs discovered during crawling.

    This pipeline handles OffsiteCandidateItem objects, which represent URLs
    that point outside the primary crawl domain (e.g., external links from uconn.edu).
    It batches these items and saves them to Delta Lake for future classification.

    Features:
    - Batch processing for efficient Delta Lake writes
    - Only processes OffsiteCandidateItem objects (ignores other item types)
    - Saves to stage1_offsite_candidates Delta table
    - Graceful shutdown with data flushing
    """

    BATCH_SIZE = 100  # Number of items to batch before writing

    def __init__(self):
        """Initialize the offsite candidate pipeline."""
        from src.common.delta_lake import get_delta_manager

        self.delta = get_delta_manager()
        self.batch = []
        self.items_processed = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "OffsiteCandidatePipeline":
        """Factory method to create pipeline instance from crawler.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            Configured OffsiteCandidatePipeline instance
        """
        pipeline = cls()

        # Connect lifecycle methods to Scrapy signals
        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Process offsite candidate items and batch them for Delta Lake.

        Args:
            item: The scraped item (only processes OffsiteCandidateItem)
            spider: The spider that yielded the item

        Returns:
            The original item (to allow subsequent pipelines to process it)
        """
        # Only process OffsiteCandidateItem objects
        if not isinstance(item, OffsiteCandidateItem):
            return item

        # Convert item to dictionary
        adapter = ItemAdapter(item)
        item_dict = adapter.asdict()

        # Add to batch
        self.batch.append(item_dict)
        self.items_processed += 1

        # Save batch if it reaches BATCH_SIZE
        if len(self.batch) >= self.BATCH_SIZE:
            self._save_batch()

        # Log progress every 500 items
        if self.items_processed % 500 == 0:
            logger.info(f"Processed {self.items_processed} offsite candidates")

        return item

    def _save_batch(self):
        """Save current batch to Delta Lake."""
        if not self.batch:
            return

        batch_size = len(self.batch)

        try:
            self.delta.write("stage1_offsite_candidates", self.batch, mode="append")
            logger.info(f"✅ Saved {batch_size} offsite candidates to Delta Lake")

            # Increment Prometheus metric
            try:
                from src.scrapy_prometheus import OFFSITE_CANDIDATES_SAVED

                if OFFSITE_CANDIDATES_SAVED:
                    OFFSITE_CANDIDATES_SAVED.labels(spider="scout").inc(batch_size)
            except ImportError:
                pass

            self.batch.clear()  # Clear batch on success
        except Exception as e:
            logger.error(f"Failed to save offsite candidates batch: {e}")

    def spider_closed(self, spider: Spider) -> None:
        """Flush remaining batch when spider closes.

        Args:
            spider: The spider that was closed
        """
        logger.info(f"Closing OffsiteCandidatePipeline for spider: {spider.name}")

        # Save any remaining items in the batch
        if self.batch:
            self._save_batch()

        logger.info(f"OffsiteCandidatePipeline stats - Total processed: {self.items_processed}")


class GrafanaSummaryPipeline:
    """Pipeline for generating random summaries of scraped content for Grafana monitoring.

    This pipeline samples scraped items from Stage 3 and generates periodic summaries
    that are exposed via Prometheus metrics for long-term qualitative monitoring in Grafana.

    Features:
    - Random sampling (every 1000th item or configurable rate)
    - Batch summarization (generates summary after N samples)
    - Prometheus integration via CRAWLER_CONTENT_SUMMARY metric
    - Text truncation for manageable summary size
    """

    SAMPLE_RATE = 1000  # Process every 1000th item
    BATCH_SIZE = 10  # Generate summary after 10 samples
    MAX_CONTENT_LENGTH = 500  # Maximum characters per sample

    def __init__(self):
        """Initialize the Grafana summary pipeline."""
        self.items_processed = 0
        self.sampled_content = []
        import random

        self.random = random

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "GrafanaSummaryPipeline":
        """Factory method to create pipeline instance from crawler.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            Configured GrafanaSummaryPipeline instance
        """
        pipeline = cls()
        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)
        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Sample and summarize items for Grafana monitoring.

        Args:
            item: The scraped item
            spider: The spider that yielded the item

        Returns:
            The original item (to allow subsequent pipelines to process it)
        """
        # Only process items from Stage 3 (check for stage3-specific fields or item types)
        # For now, process all items except OffsiteCandidateItem
        if isinstance(item, OffsiteCandidateItem):
            return item

        self.items_processed += 1

        # Random sampling: process every SAMPLE_RATE items
        if self.items_processed % self.SAMPLE_RATE == 0:
            # Extract text content from item
            adapter = ItemAdapter(item)
            text_content = self._extract_text_content(adapter)

            if text_content:
                # Truncate content to MAX_CONTENT_LENGTH
                truncated_content = text_content[: self.MAX_CONTENT_LENGTH]
                if len(text_content) > self.MAX_CONTENT_LENGTH:
                    truncated_content += "..."

                self.sampled_content.append(truncated_content)
                logger.debug(f"Sampled content from item #{self.items_processed}")

                # Generate summary when batch size is reached
                if len(self.sampled_content) >= self.BATCH_SIZE:
                    self._generate_and_export_summary(spider)

        return item

    def _extract_text_content(self, adapter: ItemAdapter) -> str:
        """Extract text content from item.

        Args:
            adapter: ItemAdapter wrapping the item

        Returns:
            Extracted text content or empty string
        """
        # Try common text field names
        text_fields = ["text", "content", "body", "description", "summary", "title"]

        for field in text_fields:
            if field in adapter and adapter.get(field):
                value = adapter.get(field)
                if isinstance(value, str):
                    return value.strip()

        # If no text field found, try to get URL as fallback
        if "url" in adapter:
            return f"URL: {adapter.get('url')}"

        return ""

    def _generate_and_export_summary(self, spider: Spider):
        """Generate summary from sampled content and export to Prometheus.

        Args:
            spider: The spider instance
        """
        if not self.sampled_content:
            return

        # Generate simple summary by concatenating samples
        summary = " | ".join(self.sampled_content)

        # Truncate summary if too long (Prometheus label values should be reasonably short)
        MAX_SUMMARY_LENGTH = 2000
        if len(summary) > MAX_SUMMARY_LENGTH:
            summary = summary[:MAX_SUMMARY_LENGTH] + "..."

        # Export to Prometheus
        try:
            from src.scrapy_prometheus import CRAWLER_CONTENT_SUMMARY

            if CRAWLER_CONTENT_SUMMARY:
                # Note: Prometheus Gauge doesn't accept string values directly
                # Instead, we'll set a numeric value and log the summary
                # For actual text display in Grafana, you'd typically use an Info metric
                # or store the summary in a separate system
                CRAWLER_CONTENT_SUMMARY.labels(spider=spider.name).set(len(self.sampled_content))
                logger.info(f"📊 Content Summary ({len(self.sampled_content)} samples): {summary[:200]}...")
        except ImportError:
            pass

        # Clear sampled content
        self.sampled_content = []

    def spider_closed(self, spider: Spider) -> None:
        """Generate final summary when spider closes.

        Args:
            spider: The spider that was closed
        """
        logger.info(f"Closing GrafanaSummaryPipeline for spider: {spider.name}")

        # Generate summary from remaining samples
        if self.sampled_content:
            self._generate_and_export_summary(spider)

        logger.info(f"GrafanaSummaryPipeline stats - Total items processed: {self.items_processed}")


# ============================================================================
# Part 1: High-Integrity Data Ingestion Pipelines
# ============================================================================


class SchemaValidationPipeline:
    """High-integrity validation pipeline using Pydantic schemas.

    This pipeline enforces schema-first data validation with explicit type coercion
    and mandatory field presence checks. It focuses on institutional cost data with
    strict non-negative float constraints.

    Features:
    - Pydantic-based schema validation with BaseRecordSchema
    - Automatic type coercion (currency strings → floats)
    - Range checks for cost fields (must be ≥ 0)
    - Kafka publishing of validation failures to validation_failures topic
    - Sets validation_status=True for items that pass all checks

    Configuration:
        SCHEMA_VALIDATION_ENABLED: Enable/disable this pipeline (default: True)
        VALIDATION_FAILURES_TOPIC: Kafka topic for failed items (default: 'validation_failures')
    """

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
        """Factory method to create pipeline from crawler settings.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            Configured SchemaValidationPipeline instance
        """
        enabled = crawler.settings.getbool("SCHEMA_VALIDATION_ENABLED", True)
        validation_failures_topic = crawler.settings.get("VALIDATION_FAILURES_TOPIC", "validation_failures")

        pipeline = cls(
            enabled=enabled,
            validation_failures_topic=validation_failures_topic,
        )

        # Connect lifecycle methods
        crawler.signals.connect(pipeline.open_spider, signal=signals.spider_opened)
        crawler.signals.connect(pipeline.close_spider, signal=signals.spider_closed)

        return pipeline

    def open_spider(self, spider: Spider) -> None:
        """Initialize Kafka producer for publishing validation failures.

        Args:
            spider: The spider that was opened
        """
        if not KAFKA_AVAILABLE or not self.enabled:
            logger.warning("SchemaValidationPipeline: Kafka not available or disabled")
            return

        logger.info(f"Opening SchemaValidationPipeline for spider: {spider.name}")

        # Initialize Kafka producer for validation failures
        bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

        try:
            config = {
                "bootstrap.servers": bootstrap_servers,
                "linger.ms": 10,
                "compression.type": "snappy",
                "acks": 1,
            }

            # Add security settings if present
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
        """Flush and close Kafka producer.

        Args:
            spider: The spider that was closed
        """
        logger.info(f"Closing SchemaValidationPipeline for spider: {spider.name}")

        if self.kafka_producer:
            try:
                remaining = self.kafka_producer.flush(timeout=30.0)
                if remaining > 0:
                    logger.warning(f"{remaining} validation failure messages not delivered")
            except Exception as e:
                logger.error(f"Error flushing Kafka producer: {e}")

        logger.info(
            f"SchemaValidationPipeline stats - Validated: {self.items_validated}, " f"Dropped: {self.items_dropped}"
        )

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Validate item against BaseRecordSchema and enforce integrity checks.

        Args:
            item: The scraped item to validate
            spider: The spider that yielded the item

        Returns:
            The validated item with validation_status=True

        Raises:
            DropItem: If item fails validation
        """
        if not self.enabled:
            return item

        # Skip OffsiteCandidateItem (has different schema)
        if isinstance(item, OffsiteCandidateItem):
            return item

        adapter = ItemAdapter(item)
        item_dict = adapter.asdict()

        # Attempt validation with Pydantic schema
        try:
            from src.schemas import BaseRecordSchema

            # Pre-process currency fields for coercion
            item_dict = self._coerce_currency_fields(item_dict)

            # Validate with Pydantic
            validated_record = BaseRecordSchema(**item_dict)

            # Mark as validated
            validated_record.validation_status = True

            # Update item with validated data
            validated_dict = validated_record.model_dump(mode="json")
            for key, value in validated_dict.items():
                adapter[key] = value

            self.items_validated += 1

            if self.items_validated % 1000 == 0:
                logger.info(
                    f"SchemaValidation stats - Validated: {self.items_validated}, " f"Dropped: {self.items_dropped}"
                )

            return item

        except ValidationError as e:
            # Extract validation error details
            self.items_dropped += 1

            # Publish validation failure to Kafka
            self._publish_validation_failure(item_dict, e, spider)

            # Drop the item
            raise DropItem(f"Schema validation failed for {item_dict.get('url', 'unknown')}: {e}")

    def _coerce_currency_fields(self, item_dict: dict[str, Any]) -> dict[str, Any]:
        """Coerce currency string fields to floats.

        Handles fields like tuition_cost, housing_cost, fees_cost, total_cost.
        Strips currency symbols ($, £, €, ¥) and commas before conversion.

        Args:
            item_dict: Item dictionary

        Returns:
            Item dictionary with coerced currency fields
        """
        currency_fields = ["tuition_cost", "housing_cost", "fees_cost", "total_cost"]
        currency_pattern = re.compile(r"[\$£€¥,\s]+")

        for field in currency_fields:
            if field in item_dict and isinstance(item_dict[field], str):
                value = item_dict[field]
                # Remove currency symbols and commas
                cleaned = currency_pattern.sub("", value)
                try:
                    item_dict[field] = float(cleaned)
                except ValueError:
                    logger.warning(f"Failed to coerce {field}='{value}' to float, leaving as-is")

        return item_dict

    def _publish_validation_failure(self, item_dict: dict[str, Any], error: ValidationError, spider: Spider) -> None:
        """Publish validation failure event to Kafka.

        Args:
            item_dict: The item that failed validation
            error: Pydantic ValidationError
            spider: The spider instance
        """
        if not self.kafka_producer:
            return

        try:
            from src.schemas import ValidationFailureRecord

            # Extract first error for simplicity
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

            # Serialize and publish
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
    """Pipeline for calculating recency-weighted scores for temporal relevance.

    This pipeline applies exponential decay scoring to items based on publication_date,
    enabling chronologically-aware aggregation and prioritization of fresh content.

    Features:
    - Exponential decay scoring: S = e^(-k * T)
    - Configurable decay constant (k) for tuning decay rate
    - Adds recency_score field [0.0, 1.0] to all items
    - Handles missing publication_date gracefully (assigns default score)

    Configuration:
        RECENCY_DECAY_CONSTANT: Decay rate (default: 0.01, ~63% after 100 days)
        RECENCY_DEFAULT_SCORE: Score for items without publication_date (default: 0.5)
    """

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
        """Factory method to create pipeline from crawler settings.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            Configured RecencyScoringPipeline instance
        """
        decay_constant = crawler.settings.getfloat("RECENCY_DECAY_CONSTANT", 0.01)
        default_score = crawler.settings.getfloat("RECENCY_DEFAULT_SCORE", 0.5)

        return cls(
            decay_constant=decay_constant,
            default_score=default_score,
        )

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Calculate and add recency_score to item.

        Args:
            item: The scraped item
            spider: The spider that yielded the item

        Returns:
            The item with recency_score added
        """
        # Skip OffsiteCandidateItem
        if isinstance(item, OffsiteCandidateItem):
            return item

        adapter = ItemAdapter(item)

        # Get publication_date
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
            # No publication_date, use default score
            adapter["recency_score"] = self.default_score

        self.items_scored += 1

        if self.items_scored % 1000 == 0:
            logger.info(f"RecencyScoring: Scored {self.items_scored} items")

        return item


class AggregationPipeline:
    """Pipeline for entity grouping and recency-weighted aggregation.

    This pipeline groups items by entity_id and sorts them by recency_score,
    then triggers batch LLM summarization on spider close.

    Features:
    - Groups items by entity_id
    - Sorts within groups by recency_score (descending)
    - Triggers LLM summarization on spider_closed
    - LLM prompt prioritizes facts with higher recency_score

    Configuration:
        AGGREGATION_ENABLED: Enable/disable aggregation (default: True)
        AGGREGATION_OUTPUT_TOPIC: Kafka topic for summaries (default: 'entity_summaries')
    """

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
        """Factory method to create pipeline from crawler settings.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            Configured AggregationPipeline instance
        """
        enabled = crawler.settings.getbool("AGGREGATION_ENABLED", True)
        output_topic = crawler.settings.get("AGGREGATION_OUTPUT_TOPIC", "entity_summaries")

        pipeline = cls(enabled=enabled, output_topic=output_topic)

        # Connect to spider_closed signal
        crawler.signals.connect(pipeline.close_spider, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Group items by entity_id for later summarization.

        Args:
            item: The scraped item
            spider: The spider that yielded the item

        Returns:
            The original item (unmodified)
        """
        if not self.enabled:
            return item

        # Skip OffsiteCandidateItem
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
        """Trigger batch summarization of aggregated entities.

        This method sorts items within each entity group by recency_score
        and then generates LLM summaries that prioritize fresher facts.

        Args:
            spider: The spider that was closed
        """
        if not self.enabled:
            return

        logger.info(f"Closing AggregationPipeline for spider: {spider.name}")
        logger.info(f"Aggregated {self.items_aggregated} items into " f"{len(self.entity_groups)} entity groups")

        # Sort items within each group by recency_score (descending)
        for entity_id, items in self.entity_groups.items():
            items.sort(key=lambda x: x.get("recency_score", 0.0), reverse=True)

            # Generate summary for this entity
            summary = self._generate_entity_summary(entity_id, items)

            if summary:
                logger.info(f"Entity {entity_id}: Generated summary from {len(items)} items")
                # In production, publish summary to Kafka or store in database
                # For now, just log it
                logger.debug(f"Summary: {summary[:200]}...")

    def _generate_entity_summary(self, entity_id: str, items: list[dict[str, Any]]) -> str:
        """Generate LLM summary for an entity, prioritizing recent facts.

        This is a placeholder for actual LLM integration. In production,
        this would call an LLM API with a prompt that instructs the model
        to prioritize information from items with higher recency_score.

        Args:
            entity_id: Entity identifier
            items: List of items for this entity, sorted by recency_score desc

        Returns:
            Generated summary text
        """
        # Placeholder implementation
        # In production, would use OpenAI, Anthropic, or other LLM API

        # Build context with recency weighting in prompt
        context_parts = []
        for item in items[:10]:  # Limit to top 10 most recent
            recency = item.get("recency_score", 0.0)
            title = item.get("title", "")
            content = item.get("content", "")[:200]  # Truncate
            context_parts.append(f"[Recency: {recency:.2f}] {title}: {content}")

        context = "\n".join(context_parts)

        # Placeholder prompt
        prompt = f"""Synthesize the following information about entity '{entity_id}'.
Prioritize facts from entries with higher recency scores (closer to 1.0).

{context}

Summary:"""

        # In production: summary = llm_api.generate(prompt)
        # For now, return placeholder
        return f"Summary for {entity_id} based on {len(items)} sources (most recent first)"


class MetadataExtractionPipeline:
    """Pipeline for extracting metadata from Stage 2 output before Stage 3/4.

    This pipeline enriches content with extracted metadata (keywords, entities, etc.)
    before downstream processing. It operates between Stage 2 and Stage 3, adding
    structured metadata to improve Stage 3/4 analysis quality.

    Features:
    - Keyword extraction using YAKE or spaCy (configurable via interface)
    - Entity extraction (persons, organizations, locations)
    - Batch processing for efficiency
    - Saves enriched data to metadata_queue Delta table
    - Graceful shutdown with data flushing

    Configuration:
        METADATA_EXTRACTION_ENABLED: Enable/disable this pipeline (default: True)
        METADATA_EXTRACTOR_TYPE: Type of extractor ('yake', 'spacy') (default: 'yake')
        METADATA_BATCH_SIZE: Batch size before writing (default: 100)
        METADATA_MAX_KEYWORDS: Max keywords per document (default: 10)
    """

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

        # Initialize keyword extractor
        self.extractor = self._init_extractor(extractor_type)

    def _init_extractor(self, extractor_type: str):
        """Initialize keyword extraction interface.

        Args:
            extractor_type: Type of extractor ('yake' or 'spacy')

        Returns:
            Extractor instance
        """
        if extractor_type == "yake":
            try:
                import yake

                # Configure YAKE extractor
                return yake.KeywordExtractor(
                    lan="en",
                    n=3,  # Max n-gram size
                    dedupLim=0.9,  # Deduplication threshold
                    top=self.max_keywords,
                    features=None,
                )
            except ImportError:
                logger.warning("YAKE not installed, falling back to simple extractor")
                return None
        elif extractor_type == "spacy":
            try:
                import spacy

                # Load spaCy model
                return spacy.load("en_core_web_sm")
            except (ImportError, OSError):
                logger.warning("spaCy not available, falling back to simple extractor")
                return None
        else:
            logger.warning(f"Unknown extractor type: {extractor_type}, using simple extractor")
            return None

    @classmethod
    def from_crawler(cls, crawler: "Crawler") -> "MetadataExtractionPipeline":
        """Factory method to create pipeline from crawler settings.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            Configured MetadataExtractionPipeline instance
        """
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

        # Connect lifecycle methods
        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        """Extract metadata from item and batch for Delta Lake.

        Args:
            item: The scraped item (from Stage 2)
            spider: The spider that yielded the item

        Returns:
            The original item (enriched with metadata field)
        """
        if not self.enabled:
            return item

        # Skip items without text content
        adapter = ItemAdapter(item)
        text_content = adapter.get("content") or adapter.get("text") or adapter.get("body")

        if not text_content or not isinstance(text_content, str):
            return item

        # Extract metadata
        metadata = self._extract_metadata(text_content, adapter)

        # Add metadata to item
        adapter["extracted_metadata"] = metadata

        # Prepare record for metadata_queue
        record = {
            "url": adapter.get("url"),
            "title": adapter.get("title", ""),
            "keywords": metadata.get("keywords", []),
            "entities": metadata.get("entities", {}),
            "extraction_timestamp": datetime.utcnow().isoformat() + "Z",
            "spider_name": spider.name,
        }

        # Add to batch
        self.batch.append(record)
        self.items_processed += 1

        # Save batch if it reaches batch_size
        if len(self.batch) >= self.batch_size:
            self._save_batch()

        # Log progress
        if self.items_processed % 500 == 0:
            logger.info(f"[METADATA] Processed {self.items_processed} items, extracted metadata from {len(self.batch)} pending")

        return item

    def _extract_metadata(self, text: str, adapter: ItemAdapter) -> dict[str, Any]:
        """Extract keywords and entities from text.

        Args:
            text: Text content to analyze
            adapter: ItemAdapter for accessing other fields

        Returns:
            Dictionary with extracted metadata
        """
        metadata = {"keywords": [], "entities": {}}

        # Extract keywords
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
        """Extract keywords using YAKE.

        Args:
            text: Text to analyze

        Returns:
            List of keyword strings
        """
        try:
            # Extract keywords with YAKE
            keywords_with_scores = self.extractor.extract_keywords(text)
            # Return only keyword text (not scores)
            return [kw for kw, score in keywords_with_scores[: self.max_keywords]]
        except Exception as e:
            logger.warning(f"YAKE extraction failed: {e}")
            return self._extract_keywords_simple(text)

    def _extract_keywords_spacy(self, text: str) -> tuple[list[str], dict[str, list[str]]]:
        """Extract keywords and entities using spaCy.

        Args:
            text: Text to analyze

        Returns:
            Tuple of (keywords list, entities dict)
        """
        try:
            doc = self.extractor(text[: 1000000])  # Limit text length for spaCy

            # Extract noun phrases as keywords
            keywords = []
            for chunk in doc.noun_chunks:
                if len(keywords) < self.max_keywords:
                    keywords.append(chunk.text.lower())

            # Extract named entities
            entities = defaultdict(list)
            for ent in doc.ents:
                entities[ent.label_].append(ent.text)

            return keywords, dict(entities)

        except Exception as e:
            logger.warning(f"spaCy extraction failed: {e}")
            return self._extract_keywords_simple(text), {}

    def _extract_keywords_simple(self, text: str) -> list[str]:
        """Simple keyword extraction fallback using word frequency.

        Args:
            text: Text to analyze

        Returns:
            List of top words by frequency
        """
        from collections import Counter

        # Simple tokenization
        words = re.findall(r"\b[a-z]{4,}\b", text.lower())

        # Filter common stop words
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

        # Get top N by frequency
        counter = Counter(filtered_words)
        top_keywords = [word for word, count in counter.most_common(self.max_keywords)]

        return top_keywords

    def _save_batch(self):
        """Save current batch to Delta Lake metadata_queue table."""
        if not self.batch:
            return

        batch_size = len(self.batch)

        try:
            from src.common.delta_lake import get_delta_manager

            delta = get_delta_manager()
            delta.write("metadata_queue", self.batch, mode="append")
            logger.info(f"✅ Saved {batch_size} metadata records to metadata_queue")

            self.batch.clear()  # Clear batch on success
        except Exception as e:
            logger.error(f"Failed to save metadata batch: {e}")

    def spider_closed(self, spider: Spider) -> None:
        """Flush remaining batch when spider closes.

        Args:
            spider: The spider that was closed
        """
        logger.info(f"[METADATA] Closing MetadataExtractionPipeline for spider: {spider.name}")

        # Save any remaining items in the batch
        if self.batch:
            self._save_batch()

        logger.info(f"[METADATA] Pipeline stats - Total processed: {self.items_processed}")
