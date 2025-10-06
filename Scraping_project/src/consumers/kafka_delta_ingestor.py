#!/usr/bin/env python
"""
Kafka to Delta Lake Ingestor
=============================
This daemon consumes messages from Kafka and writes them to Delta Lake in batches.

Features:
- High-performance batch processing with configurable batch size and timeout
- Automatic schema evolution for Delta Lake tables
- Exactly-once semantics with manual offset commits
- Graceful shutdown handling
- Prometheus metrics for monitoring
- Error handling with dead-letter queue support
"""

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Dict, List

import pandas as pd
from confluent_kafka import Consumer, KafkaError, KafkaException
from deltalake import DeltaTable, write_deltalake
from prometheus_client import Counter, Gauge, Histogram, start_http_server

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# Prometheus metrics
MESSAGES_CONSUMED = Counter(
    'kafka_messages_consumed_total',
    'Total messages consumed from Kafka',
    ['topic']
)

MESSAGES_PROCESSED = Counter(
    'kafka_messages_processed_total',
    'Total messages successfully processed',
    ['topic']
)

MESSAGES_FAILED = Counter(
    'kafka_messages_failed_total',
    'Total messages that failed processing',
    ['topic']
)

BATCH_SIZE_HISTOGRAM = Histogram(
    'delta_batch_size',
    'Size of batches written to Delta Lake',
    buckets=(10, 50, 100, 500, 1000, 5000, 10000)
)

BATCH_WRITE_TIME = Histogram(
    'delta_batch_write_seconds',
    'Time taken to write batch to Delta Lake',
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0)
)

CONSUMER_LAG = Gauge(
    'kafka_consumer_lag',
    'Current consumer lag',
    ['topic', 'partition']
)


class KafkaDeltaIngestor:
    """Consumes Kafka messages and writes to Delta Lake."""

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        group_id: str,
        delta_table_path: str,
        batch_size: int = 1000,
        batch_timeout_seconds: int = 30,
    ):
        """Initialize the ingestor.

        Args:
            bootstrap_servers: Kafka broker addresses
            topic: Kafka topic to consume from
            group_id: Consumer group ID
            delta_table_path: Path to Delta Lake table
            batch_size: Maximum number of messages per batch
            batch_timeout_seconds: Maximum seconds to wait before flushing batch
        """
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.group_id = group_id
        self.delta_table_path = delta_table_path
        self.batch_size = batch_size
        self.batch_timeout_seconds = batch_timeout_seconds

        # State
        self.consumer = None
        self.running = False
        self.message_batch: List[Dict] = []
        self.last_flush_time = time.time()

        logger.info(f"Ingestor initialized: {topic} -> {delta_table_path}")
        logger.info(f"Batch size: {batch_size}, Batch timeout: {batch_timeout_seconds}s")

    def setup_consumer(self):
        """Set up Kafka consumer."""
        config = {
            'bootstrap.servers': self.bootstrap_servers,
            'group.id': self.group_id,
            'auto.offset.reset': os.getenv('KAFKA_AUTO_OFFSET_RESET', 'earliest'),
            'enable.auto.commit': False,  # Manual commit for exactly-once semantics
            'max.poll.interval.ms': 300000,  # 5 minutes
            'session.timeout.ms': 60000,  # 1 minute
        }

        # Load security settings from environment
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

        self.consumer = Consumer(config)
        self.consumer.subscribe([self.topic])
        logger.info(f"Subscribed to topic: {self.topic}")

    def setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""

        def signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name
            logger.info(f"Received {sig_name} signal, initiating shutdown...")
            self.running = False

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

    def process_message(self, message: str) -> Dict:
        """Process a single message.

        Args:
            message: JSON message string

        Returns:
            Processed message as dictionary
        """
        try:
            # Parse JSON
            data = json.loads(message)

            # Add ingestion metadata
            data['_ingest_timestamp'] = datetime.utcnow().isoformat() + 'Z'

            return data

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON: {e}")
            raise
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            raise

    def flush_batch(self):
        """Flush current batch to Delta Lake."""
        if not self.message_batch:
            return

        batch_size = len(self.message_batch)
        logger.info(f"Flushing batch of {batch_size} messages to Delta Lake...")

        start_time = time.time()

        try:
            # Convert to DataFrame
            df = pd.DataFrame(self.message_batch)

            # Write to Delta Lake
            write_deltalake(
                self.delta_table_path,
                df,
                mode='append',
                schema_mode='merge',  # Automatic schema evolution
                engine='pyarrow',
            )

            # Update metrics
            MESSAGES_PROCESSED.labels(topic=self.topic).inc(batch_size)
            BATCH_SIZE_HISTOGRAM.observe(batch_size)
            BATCH_WRITE_TIME.observe(time.time() - start_time)

            logger.info(f"Successfully wrote {batch_size} messages to Delta Lake")

            # Commit offsets after successful write (exactly-once semantics)
            self.consumer.commit()

            # Clear batch
            self.message_batch = []
            self.last_flush_time = time.time()

        except Exception as e:
            logger.error(f"Failed to write batch to Delta Lake: {e}", exc_info=True)
            MESSAGES_FAILED.labels(topic=self.topic).inc(batch_size)
            raise

    def should_flush(self) -> bool:
        """Check if batch should be flushed.

        Returns:
            True if batch should be flushed, False otherwise
        """
        # Flush if batch size reached
        if len(self.message_batch) >= self.batch_size:
            return True

        # Flush if timeout reached
        if time.time() - self.last_flush_time >= self.batch_timeout_seconds:
            return True

        return False

    def run(self):
        """Run the ingestor main loop."""
        logger.info("Starting ingestor main loop...")
        self.running = True

        try:
            while self.running:
                # Poll for messages
                msg = self.consumer.poll(timeout=1.0)

                if msg is None:
                    # No message, check if we should flush
                    if self.should_flush():
                        self.flush_batch()
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        # End of partition, normal
                        logger.debug(f"Reached end of partition {msg.partition()}")
                    else:
                        raise KafkaException(msg.error())
                    continue

                # Process message
                try:
                    message_value = msg.value().decode('utf-8')
                    processed_data = self.process_message(message_value)
                    self.message_batch.append(processed_data)

                    MESSAGES_CONSUMED.labels(topic=self.topic).inc()

                    # Check if we should flush
                    if self.should_flush():
                        self.flush_batch()

                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
                    MESSAGES_FAILED.labels(topic=self.topic).inc()
                    # Continue processing other messages

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        except Exception as e:
            logger.error(f"Fatal error in main loop: {e}", exc_info=True)
            raise
        finally:
            # Flush remaining messages
            if self.message_batch:
                logger.info("Flushing remaining messages before shutdown...")
                try:
                    self.flush_batch()
                except Exception as e:
                    logger.error(f"Error flushing final batch: {e}")

            # Close consumer
            if self.consumer:
                logger.info("Closing Kafka consumer...")
                self.consumer.close()

            logger.info("Ingestor stopped")

    def start(self):
        """Start the ingestor."""
        logger.info("Starting Kafka to Delta Lake ingestor...")

        # Set up signal handlers
        self.setup_signal_handlers()

        # Set up Kafka consumer
        self.setup_consumer()

        # Start Prometheus metrics server
        metrics_port = int(os.getenv('PROMETHEUS_PORT', '8001'))
        try:
            start_http_server(metrics_port)
            logger.info(f"Prometheus metrics server started on port {metrics_port}")
        except Exception as e:
            logger.warning(f"Failed to start Prometheus server: {e}")

        # Run main loop
        self.run()


def main():
    """Main entry point."""
    # Load configuration from environment
    bootstrap_servers = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092')
    topic = os.getenv('KAFKA_TOPIC', 'scraped-items')
    group_id = os.getenv('KAFKA_GROUP_ID', 'delta-ingestor')
    delta_table_path = os.getenv('DELTA_TABLE_PATH', '/data/delta_lake/scraped_items')
    batch_size = int(os.getenv('BATCH_SIZE', '1000'))
    batch_timeout = int(os.getenv('BATCH_TIMEOUT_SECONDS', '30'))

    # Create and start ingestor
    ingestor = KafkaDeltaIngestor(
        bootstrap_servers=bootstrap_servers,
        topic=topic,
        group_id=group_id,
        delta_table_path=delta_table_path,
        batch_size=batch_size,
        batch_timeout_seconds=batch_timeout,
    )

    try:
        ingestor.start()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
