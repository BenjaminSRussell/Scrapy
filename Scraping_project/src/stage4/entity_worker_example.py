"""Example integration of Stage 4 Entity Summarization into the pipeline.

This script demonstrates how to integrate the entity-centric summarization
pipeline with the existing Scrapy/Kafka/Delta Lake infrastructure.

Integration Points:
1. Consume from Delta Lake (stage3_analytics or stage4_large_docs)
2. Extract entity information and content
3. Process through entity summarization pipeline
4. Store results in entity_summaries Delta Lake table

Usage:
    python -m src.stage4.entity_worker_example
"""

import logging
from datetime import datetime

from src.common.delta_lake import get_delta_manager
from src.stage4.entity_summarization import Stage4EntityWorker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


class EntityWorkerRunner:
    """Runs the entity summarization worker on data from Delta Lake."""

    def __init__(
        self,
        input_table: str = "stage3_analytics",
        batch_size: int = 100,
    ):
        """Initialize the runner.

        Args:
            input_table: Delta Lake table to read from
            batch_size: Number of documents to process in each batch
        """
        self.input_table = input_table
        self.batch_size = batch_size

        # Initialize Delta Lake manager
        self.delta = get_delta_manager()

        # Initialize Stage 4 worker
        self.worker = Stage4EntityWorker(
            embedding_model="sentence-transformers/all-MiniLM-L6-v2",
            summarization_model="facebook/bart-large-cnn",
            similarity_threshold=0.85,
            device=-1,  # CPU
        )

    def extract_entity_from_record(self, record: dict) -> tuple[str, str]:
        """Extract entity name and type from a Delta Lake record.

        This is a simple implementation. For production, use NER (Named Entity Recognition)
        to automatically identify entities in the content.

        Args:
            record: Delta Lake record

        Returns:
            Tuple of (entity_name, entity_type)
        """
        # Option 1: Use explicit entity_id field if available
        entity_id = record.get("entity_id")
        if entity_id:
            # Parse entity_id (e.g., "person:jane_doe" or "org:uconn_ai_lab")
            if ":" in entity_id:
                entity_type, entity_name = entity_id.split(":", 1)
                return entity_name.replace("_", " ").title(), entity_type
            else:
                return entity_id, "unknown"

        # Option 2: Use title as entity name (fallback)
        title = record.get("title", "Unknown Entity")
        return title, "unknown"

    def run(self, limit: int | None = None):
        """Run the entity worker on data from Delta Lake.

        Args:
            limit: Maximum number of records to process (None for all)
        """
        logger.info(f"Reading from Delta Lake table: {self.input_table}")

        # Read records from Delta Lake
        records = self.delta.read(self.input_table)

        if limit:
            records = records[:limit]

        logger.info(f"Found {len(records)} records to process")

        if not records:
            logger.warning("No records found. Exiting.")
            return

        # Process in batches
        for i in range(0, len(records), self.batch_size):
            batch = records[i:i + self.batch_size]
            logger.info(f"Processing batch {i // self.batch_size + 1}: {len(batch)} records")

            # Convert Delta Lake records to document format
            documents = []
            for record in batch:
                entity_name, entity_type = self.extract_entity_from_record(record)

                # Extract content (try multiple field names)
                content = (
                    record.get("content") or
                    record.get("combined_text") or
                    record.get("summary") or
                    ""
                )

                if not content:
                    logger.warning(f"No content found for record: {record.get('url')}")
                    continue

                # Parse publication date
                pub_date = record.get("publication_date")
                if isinstance(pub_date, str):
                    try:
                        pub_date = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                    except ValueError:
                        pub_date = None

                documents.append({
                    "entity_name": entity_name,
                    "entity_type": entity_type,
                    "content": content,
                    "source_url": record.get("url", record.get("source_url", "unknown")),
                    "publication_date": pub_date,
                    "metadata": {
                        "title": record.get("title"),
                        "scraped_at": record.get("scraped_at_utc"),
                        "spider_name": record.get("spider_name"),
                    }
                })

            # Process batch through entity worker
            if documents:
                self.worker.process_documents(documents)

        logger.info("✅ Entity worker processing complete")


class KafkaEntityWorker:
    """Alternative integration: Consume from Kafka topic and process entities.

    This class demonstrates how to integrate the entity summarization pipeline
    with Kafka for real-time processing.
    """

    def __init__(
        self,
        kafka_topic: str = "final_categorized",
        consumer_group: str = "entity-worker-group",
        bootstrap_servers: str = "localhost:9092",
    ):
        """Initialize Kafka consumer.

        Args:
            kafka_topic: Kafka topic to consume from
            consumer_group: Consumer group ID
            bootstrap_servers: Kafka bootstrap servers
        """
        self.kafka_topic = kafka_topic
        self.consumer_group = consumer_group
        self.bootstrap_servers = bootstrap_servers

        # Initialize worker
        self.worker = Stage4EntityWorker()

        # Batch accumulator
        self.document_batch = []
        self.batch_size = 50

    def start_consuming(self):
        """Start consuming from Kafka and processing entities.

        This is a template - in production, use confluent_kafka for better performance.
        """
        try:
            from confluent_kafka import Consumer, KafkaError
        except ImportError:
            logger.error("confluent_kafka not installed. Install with: pip install confluent-kafka")
            return

        # Configure consumer
        consumer_config = {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.consumer_group,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": True,
        }

        consumer = Consumer(consumer_config)
        consumer.subscribe([self.kafka_topic])

        logger.info(f"Started consuming from Kafka topic: {self.kafka_topic}")

        try:
            while True:
                msg = consumer.poll(timeout=1.0)

                if msg is None:
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    else:
                        logger.error(f"Kafka error: {msg.error()}")
                        break

                # Parse message
                import json
                record = json.loads(msg.value().decode("utf-8"))

                # Extract entity info (same logic as EntityWorkerRunner)
                entity_name = record.get("entity_id", record.get("title", "Unknown"))
                entity_type = record.get("entity_type", "unknown")

                document = {
                    "entity_name": entity_name,
                    "entity_type": entity_type,
                    "content": record.get("content", ""),
                    "source_url": record.get("url", "unknown"),
                    "publication_date": record.get("publication_date"),
                    "metadata": {
                        "title": record.get("title"),
                        "category": record.get("category_final"),
                    }
                }

                self.document_batch.append(document)

                # Process batch when full
                if len(self.document_batch) >= self.batch_size:
                    logger.info(f"Processing batch of {len(self.document_batch)} documents")
                    self.worker.process_documents(self.document_batch)
                    self.document_batch.clear()

        except KeyboardInterrupt:
            logger.info("Shutting down consumer...")
        finally:
            # Process remaining documents
            if self.document_batch:
                self.worker.process_documents(self.document_batch)

            consumer.close()


def main():
    """Main entry point for the entity worker."""
    import argparse

    parser = argparse.ArgumentParser(description="Run Stage 4 Entity Summarization Worker")
    parser.add_argument(
        "--mode",
        choices=["delta", "kafka"],
        default="delta",
        help="Processing mode: delta (batch) or kafka (streaming)"
    )
    parser.add_argument(
        "--input-table",
        default="stage3_analytics",
        help="Delta Lake table to read from (delta mode only)"
    )
    parser.add_argument(
        "--kafka-topic",
        default="final_categorized",
        help="Kafka topic to consume from (kafka mode only)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of records to process (delta mode only)"
    )

    args = parser.parse_args()

    if args.mode == "delta":
        # Batch processing from Delta Lake
        runner = EntityWorkerRunner(input_table=args.input_table)
        runner.run(limit=args.limit)

    elif args.mode == "kafka":
        # Streaming processing from Kafka
        worker = KafkaEntityWorker(kafka_topic=args.kafka_topic)
        worker.start_consuming()


if __name__ == "__main__":
    main()
