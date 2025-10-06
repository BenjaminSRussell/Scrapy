"""Delta Lake Consumer - Standalone consumer for writing to Delta Lake.

This consumer reads from Redis message queues and writes to Delta Lake,
decoupling data flow and preventing write conflicts.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.config import get_config
from src.common.delta_lake import get_delta_manager
from src.common.redis_manager import get_redis_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DeltaConsumer:
    """Consumer that reads from Redis queues and writes to Delta Lake."""

    def __init__(
        self,
        queue_name: str,
        delta_table: str,
        batch_size: int = 100,
        poll_interval: float = 1.0,
    ):
        """Initialize consumer.

        Args:
            queue_name: Redis queue to consume from
            delta_table: Delta Lake table to write to
            batch_size: Number of records to batch before writing
            poll_interval: Seconds between polls
        """
        self.queue_name = queue_name
        self.delta_table = delta_table
        self.batch_size = batch_size
        self.poll_interval = poll_interval

        # Initialize managers
        config = get_config()

        redis_config = config.redis_config
        self.redis = get_redis_manager(
            host=redis_config.get('host', 'localhost'),
            port=redis_config.get('port', 6379),
            db=redis_config.get('db', 0),
            password=redis_config.get('password'),
        )

        self.delta = get_delta_manager()

        # Graceful shutdown
        self.running = True
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        # Metrics
        self.records_processed = 0
        self.batches_written = 0
        self.errors = 0

    def _handle_shutdown(self, signum, frame):
        """Handle shutdown signal.

        Args:
            signum: Signal number
            frame: Stack frame
        """
        logger.warning(f"Received signal {signum}, shutting down gracefully...")
        self.running = False

    async def consume(self):
        """Main consume loop."""
        logger.info(
            f"Consumer started: queue={self.queue_name}, "
            f"table={self.delta_table}, batch_size={self.batch_size}"
        )

        batch: list[dict] = []

        while self.running:
            try:
                # Pop from queue (non-blocking)
                record = self.redis.pop_from_queue(self.queue_name, timeout=0)

                if record:
                    batch.append(record)
                    self.records_processed += 1

                    # Write batch when full
                    if len(batch) >= self.batch_size:
                        await self._write_batch(batch)
                        batch = []

                else:
                    # No records available
                    # Write any pending batch
                    if batch:
                        await self._write_batch(batch)
                        batch = []

                    # Sleep before next poll
                    await asyncio.sleep(self.poll_interval)

            except Exception as e:
                logger.error(f"Error consuming from queue: {e}")
                self.errors += 1
                await asyncio.sleep(5)  # Back off on error

        # Write any remaining records
        if batch:
            await self._write_batch(batch)

        logger.info(
            f"Consumer stopped: processed={self.records_processed}, "
            f"batches={self.batches_written}, errors={self.errors}"
        )

    async def _write_batch(self, batch: list[dict]):
        """Write batch to Delta Lake.

        Args:
            batch: List of records to write
        """
        try:
            self.delta.write(
                self.delta_table,
                batch,
                mode='append',
                async_write=False,
            )
            self.batches_written += 1
            logger.info(
                f"Wrote batch to {self.delta_table}: {len(batch)} records "
                f"(total: {self.records_processed})"
            )

        except Exception as e:
            logger.error(f"Failed to write batch to Delta Lake: {e}")
            self.errors += 1

            # Re-queue failed records (optional)
            # for record in batch:
            #     self.redis.push_to_queue(f"{self.queue_name}_failed", record)

    async def run(self):
        """Run consumer."""
        try:
            await self.consume()
        finally:
            # Cleanup
            self.delta.checkpoint()
            logger.info("Consumer shutdown complete")


class MultiTableConsumer:
    """Consumer that handles multiple queues/tables in parallel."""

    def __init__(self, queue_table_mapping: dict[str, str]):
        """Initialize multi-table consumer.

        Args:
            queue_table_mapping: Dict mapping queue names to table names
        """
        self.queue_table_mapping = queue_table_mapping
        self.consumers: list[DeltaConsumer] = []

        for queue, table in queue_table_mapping.items():
            consumer = DeltaConsumer(queue, table)
            self.consumers.append(consumer)

    async def run_all(self):
        """Run all consumers in parallel."""
        logger.info(f"Starting {len(self.consumers)} consumers...")

        tasks = [
            asyncio.create_task(consumer.run())
            for consumer in self.consumers
        ]

        await asyncio.gather(*tasks)


# Predefined consumer configurations
def get_default_consumers() -> dict[str, str]:
    """Get default queue-to-table mappings.

    Returns:
        Dictionary mapping queue names to Delta Lake table names
    """
    config = get_config()
    mq_config = config.message_queue_config
    delta_config = config.delta_lake_config
    tables = delta_config.get('tables', {})

    return {
        # Stage 1 discovered URLs -> stage1_discovery
        mq_config.get('stage1_to_stage2', 'stage1_discovered_urls'):
            tables.get('stage1_discovery', 'stage1_discovery'),

        # Stage 2 analyzed pages -> stage2_page_analysis
        mq_config.get('stage2_to_stage3', 'stage2_analyzed_pages'):
            tables.get('stage2_page_analysis', 'stage2_page_analysis'),

        # Stage 2 large docs -> stage4_large_docs
        mq_config.get('stage2_to_stage4', 'stage2_large_docs'):
            tables.get('stage4_large_docs', 'stage4_large_docs'),

        # Stage 3 summaries -> stage3_summaries
        'stage3_summaries':
            tables.get('stage3_summaries', 'stage3_summaries'),

        # Stage 4 summaries -> stage4_summaries
        'stage4_summaries':
            tables.get('stage4_summaries', 'stage4_summaries'),

        # Error queues
        mq_config.get('stage1_errors', 'stage1_error_queue'):
            tables.get('stage1_errors', 'stage1_errors'),
    }


async def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Delta Lake Consumer - Read from Redis and write to Delta Lake"
    )

    parser.add_argument(
        '--queue',
        type=str,
        help='Specific queue to consume from'
    )

    parser.add_argument(
        '--table',
        type=str,
        help='Specific Delta Lake table to write to'
    )

    parser.add_argument(
        '--all',
        action='store_true',
        help='Run all default consumers in parallel'
    )

    parser.add_argument(
        '--batch-size',
        type=int,
        default=100,
        help='Batch size for writing (default: 100)'
    )

    args = parser.parse_args()

    if args.all:
        # Run all default consumers
        logger.info("Starting all default consumers...")
        consumers = get_default_consumers()
        multi_consumer = MultiTableConsumer(consumers)
        await multi_consumer.run_all()

    elif args.queue and args.table:
        # Run single consumer
        logger.info(f"Starting consumer: {args.queue} -> {args.table}")
        consumer = DeltaConsumer(
            args.queue,
            args.table,
            batch_size=args.batch_size,
        )
        await consumer.run()

    else:
        parser.print_help()
        print("\nError: Must specify either --all or both --queue and --table")
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
