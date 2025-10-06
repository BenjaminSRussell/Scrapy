"""Scrapy pipelines for the scraping project.

This module contains custom pipeline implementations for processing scraped items.
"""

import json
import logging
import os
from typing import Any, Dict

from confluent_kafka import Producer
from itemadapter import ItemAdapter
from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import NotConfigured

logger = logging.getLogger(__name__)


class KafkaPipeline:
    """High-performance Kafka pipeline for streaming scraped items.

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
        producer_config: Dict[str, Any] = None,
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
        """Process and publish item to Kafka.

        This method is called by Scrapy for every item yielded by the spider.
        It serializes the item to JSON and publishes it to Kafka asynchronously.

        Args:
            item: The scraped item to process
            spider: The spider that yielded the item

        Returns:
            The original item (to allow subsequent pipelines to process it)
        """
        try:
            # Convert item to dictionary using ItemAdapter (works with dicts, scrapy Items, etc.)
            item_dict = ItemAdapter(item).asdict()

            # Add metadata
            item_dict['_spider'] = spider.name
            item_dict['_pipeline_timestamp'] = self._get_timestamp()

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

    @staticmethod
    def _get_timestamp() -> str:
        """Get ISO format timestamp.

        Returns:
            Current timestamp in ISO 8601 format
        """
        from datetime import datetime
        return datetime.utcnow().isoformat() + 'Z'
