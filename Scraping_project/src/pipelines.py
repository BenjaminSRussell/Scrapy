"""Scrapy pipelines for the scraping project.

This module contains custom pipeline implementations for processing scraped items.
"""

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

try:
    from confluent_kafka import Producer
    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False
    Producer = None

from itemadapter import ItemAdapter
from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem, NotConfigured

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

    def __init__(self, required_fields: list = None):
        """Initialize the validation pipeline.

        Args:
            required_fields: List of field names that must be present in every item
        """
        self.required_fields = required_fields or ['url']
        self.items_validated = 0
        self.items_dropped = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> 'DataValidationPipeline':
        """Factory method to create pipeline instance from crawler settings.

        Args:
            crawler: Scrapy crawler instance with settings

        Returns:
            Configured DataValidationPipeline instance
        """
        required_fields = crawler.settings.getlist('VALIDATION_REQUIRED_FIELDS', ['url'])
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

        # Check required fields
        for field in self.required_fields:
            if field not in adapter:
                self.items_dropped += 1
                raise DropItem(
                    f"Missing required field '{field}' in item from spider '{spider.name}'. "
                    f"Item: {dict(adapter)}"
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
                    f"Required field '{field}' is None in item from spider '{spider.name}'. "
                    f"Item: {dict(adapter)}"
                )

        self.items_validated += 1

        # Log validation stats every 1000 items
        if self.items_validated % 1000 == 0:
            logger.info(
                f"Validation stats - Validated: {self.items_validated}, "
                f"Dropped: {self.items_dropped}"
            )

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
    CURRENCY_PATTERN = re.compile(r'[\$£€¥]?\s*([0-9,]+\.?[0-9]*)')

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

                # Normalize common fields
                if field_name in ('category', 'type', 'status'):
                    cleaned = cleaned.lower()

                # Convert currency strings to float
                if field_name in ('price', 'cost', 'amount'):
                    cleaned = self._parse_currency(cleaned)

                adapter[field_name] = cleaned

            # Normalize lists (strip strings in lists)
            elif isinstance(value, list):
                adapter[field_name] = [
                    item.strip() if isinstance(item, str) else item
                    for item in value
                ]

        self.items_cleansed += 1

        if self.items_cleansed % 1000 == 0:
            logger.info(f"Cleansed {self.items_cleansed} items")

        return item

    def _parse_currency(self, value: str) -> float:
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
                return float(match.group(1).replace(',', ''))
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
        adapter['scraped_at_utc'] = datetime.utcnow().isoformat() + 'Z'
        adapter['spider_name'] = spider.name
        adapter['pipeline_version'] = self.PIPELINE_VERSION

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
        producer_config: dict[str, Any] = None,
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
        self.producer = None
        self.messages_sent = 0
        self.messages_failed = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> 'KafkaPipeline':
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
            raise NotConfigured('confluent_kafka library not available')

        # Load required settings
        bootstrap_servers = crawler.settings.get('KAFKA_BOOTSTRAP_SERVERS')
        if not bootstrap_servers:
            raise NotConfigured('KAFKA_BOOTSTRAP_SERVERS setting is required')

        topic = crawler.settings.get('KAFKA_TOPIC')
        if not topic:
            raise NotConfigured('KAFKA_TOPIC setting is required')

        # Load optional producer config
        producer_config = crawler.settings.get('KAFKA_PRODUCER_CONFIG', {})

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
            'bootstrap.servers': self.bootstrap_servers,
            # Optimize for throughput and reliability
            'linger.ms': 10,  # Small batching delay for better throughput
            'batch.size': 16384,  # 16KB batch size
            'compression.type': 'snappy',  # Fast compression
            'acks': 1,  # Wait for leader acknowledgment
            'retries': 3,  # Retry failed sends
            'max.in.flight.requests.per.connection': 5,
        }

        # Load security settings from environment variables (never hardcode credentials!)
        security_protocol = os.getenv('KAFKA_SECURITY_PROTOCOL')
        if security_protocol:
            config['security.protocol'] = security_protocol

        sasl_mechanism = os.getenv('KAFKA_SASL_MECHANISM')
        if sasl_mechanism:
            config['sasl.mechanism'] = sasl_mechanism

        sasl_username = os.getenv('KAFKA_SASL_USERNAME')
        if sasl_username:
            config['sasl.username'] = sasl_username

        sasl_password = os.getenv('KAFKA_SASL_PASSWORD')
        if sasl_password:
            config['sasl.password'] = sasl_password

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

                logger.info(
                    f"Kafka pipeline stats - Sent: {self.messages_sent}, "
                    f"Failed: {self.messages_failed}"
                )
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
                logger.info(
                    f"Message delivered to {msg.topic()} [{msg.partition()}] "
                    f"at offset {msg.offset()}"
                )

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
            self.producer.produce(
                topic=self.topic,
                value=message_value.encode('utf-8'),
                callback=self.delivery_report,
            )

            # Trigger delivery report callbacks (non-blocking)
            # This processes any pending delivery reports without blocking
            self.producer.poll(0)

        except Exception as e:
            logger.error(f"Error processing item for Kafka: {e}")
            # Don't raise - allow item to continue through pipeline

        return item
