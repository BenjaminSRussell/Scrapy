import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any, AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class BatchQueueItem:
    """Item in the processing queue"""
    url: str
    url_hash: str
    source_stage: str
    data: Dict[str, Any]
    created_at: str = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()


class BatchQueue:
    """Manages batch processing with backpressure rules

    DESIGN NOTE: Basic populate_*_queue methods fill completely before consumption starts,
    which blocks pipeline for >10k items at max_queue_size (10k default).
    SOLUTION: Use run_concurrent_stage*_* methods which implement concurrent
    producer/consumer pattern to avoid blocking. Consider persistent queue
    (Redis/SQLite) for very large datasets or distributed processing.
    """

    def __init__(self, batch_size: int = 1000, max_queue_size: int = 10000):
        self.batch_size = batch_size
        self.max_queue_size = max_queue_size
        self._queue = asyncio.Queue(maxsize=max_queue_size)
        self._running = False
        self._producer_done = False

    async def put(self, item: BatchQueueItem):
        """Add item to queue with backpressure"""
        if self._queue.full():
            logger.warning(f"Queue is full ({self.max_queue_size}), applying backpressure")

        await self._queue.put(item)

    async def get_batch(self) -> List[BatchQueueItem]:
        """Get a batch of items from the queue"""
        batch = []

        # Get at least one item (blocking)
        try:
            item = await self._queue.get()
            batch.append(item)
        except asyncio.QueueEmpty:
            return batch

        # Try to get more items up to batch_size (non-blocking)
        while len(batch) < self.batch_size:
            try:
                item = self._queue.get_nowait()
                batch.append(item)
            except asyncio.QueueEmpty:
                break

        return batch

    def qsize(self) -> int:
        """Get current queue size"""
        return self._queue.qsize()

    def is_empty(self) -> bool:
        """Check if queue is empty"""
        return self._queue.empty()

    def is_full(self) -> bool:
        """Check if queue is full"""
        return self._queue.full()

    def mark_producer_done(self):
        """Mark that no more items will be added to the queue"""
        self._producer_done = True

    def is_producer_done(self) -> bool:
        """Check if producer is finished adding items"""
        return self._producer_done

    async def get_batch_or_wait(self, timeout: float = 1.0) -> List[BatchQueueItem]:
        """Get a batch with timeout, handling producer completion"""
        batch = []

        try:
            # Wait for at least one item or timeout
            item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            batch.append(item)

            # Try to get more items up to batch_size (non-blocking)
            while len(batch) < self.batch_size:
                try:
                    item = self._queue.get_nowait()
                    batch.append(item)
                except asyncio.QueueEmpty:
                    break

        except asyncio.TimeoutError:
            # If producer is done and queue is empty, we're finished
            if self._producer_done and self._queue.empty():
                return []
            # Otherwise continue waiting
            pass

        return batch


class PipelineOrchestrator:
    """Orchestrates the multi-stage pipeline with batch processing"""

    def __init__(self, config):
        self.config = config
        self.stage1_to_stage2_queue = BatchQueue(
            batch_size=config.get_stage2_config()['max_workers'] or 1000
        )
        self.stage2_to_stage3_queue = BatchQueue(
            batch_size=config.get_stage3_config().get('batch_size', 1000)
        )

    async def load_stage1_results(self) -> AsyncGenerator[BatchQueueItem, None]:
        """Load Stage 1 discovery results and yield as queue items"""
        stage1_config = self.config.get_stage1_config()
        output_file = Path(stage1_config['output_file'])

        if not output_file.exists():
            logger.warning(f"Stage 1 output file not found: {output_file}")
            return

        logger.info(f"Loading Stage 1 results from {output_file}")

        with open(output_file, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())

                    item = BatchQueueItem(
                        url=data.get('discovered_url', ''),
                        url_hash=data.get('url_hash', ''),
                        source_stage='stage1',
                        data=data
                    )

                    yield item

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON at line {line_no}: {e}")
                    continue

    async def load_stage2_results(self) -> AsyncGenerator[BatchQueueItem, None]:
        """Load Stage 2 validation results and yield as queue items"""
        stage2_config = self.config.get_stage2_config()
        output_file = Path(stage2_config['output_file'])

        if not output_file.exists():
            logger.warning(f"Stage 2 output file not found: {output_file}")
            return

        logger.info(f"Loading Stage 2 results from {output_file}")

        with open(output_file, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())

                    # Only process valid URLs
                    if not data.get('is_valid', False):
                        continue

                    item = BatchQueueItem(
                        url=data.get('url', ''),
                        url_hash=data.get('url_hash', ''),
                        source_stage='stage2',
                        data=data
                    )

                    yield item

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON at line {line_no}: {e}")
                    continue

    async def populate_stage2_queue(self):
        """Populate the Stage 2 queue from Stage 1 results

        DESIGN LIMITATION: This method fills the entire queue before consumption starts.
        For >10k items, this blocks at max_queue_size (10k default).
        SOLUTION: Use run_concurrent_stage2_validation() instead for large datasets.
        """
        logger.info("Populating Stage 2 queue from Stage 1 results")

        count = 0
        try:
            async for item in self.load_stage1_results():
                await self.stage1_to_stage2_queue.put(item)
                count += 1

                if count % 1000 == 0:
                    logger.info(f"Queued {count} items for Stage 2")
        finally:
            # Mark producer as done so consumers know to stop waiting
            self.stage1_to_stage2_queue.mark_producer_done()
            logger.info(f"Finished queuing {count} items for Stage 2")

    async def populate_stage3_queue(self):
        """Populate the Stage 3 queue from Stage 2 results"""
        logger.info("Populating Stage 3 queue from Stage 2 results")

        count = 0
        try:
            async for item in self.load_stage2_results():
                await self.stage2_to_stage3_queue.put(item)
                count += 1

                if count % 1000 == 0:
                    logger.info(f"Queued {count} items for Stage 3")
        finally:
            # Mark producer as done so consumers know to stop waiting
            self.stage2_to_stage3_queue.mark_producer_done()
            logger.info(f"Finished queuing {count} items for Stage 3")

    def get_stage2_queue(self) -> BatchQueue:
        """Get the Stage 2 processing queue"""
        return self.stage1_to_stage2_queue

    def get_stage3_queue(self) -> BatchQueue:
        """Get the Stage 3 processing queue"""
        return self.stage2_to_stage3_queue

    async def run_concurrent_stage2_validation(self, validator):
        """Run Stage 2 validation with concurrent population and consumption

        This method demonstrates the fix for the >10k item bottleneck by
        running producer and consumer concurrently instead of sequentially.
        """
        logger.info("Starting concurrent Stage 2 validation")

        # Start producer and consumer concurrently
        producer_task = asyncio.create_task(self.populate_stage2_queue())
        consumer_task = asyncio.create_task(self._consume_stage2_queue(validator))

        # Wait for both to complete
        await asyncio.gather(producer_task, consumer_task)

    async def _consume_stage2_queue(self, validator):
        """Consumer for Stage 2 queue - processes batches as they become available"""
        queue = self.get_stage2_queue()
        processed_count = 0

        while True:
            # Use the new timeout-aware batch getter
            batch = await queue.get_batch_or_wait(timeout=2.0)

            if not batch:
                # No items and producer is done
                break

            await validator.validate_batch(batch)
            processed_count += len(batch)

            if processed_count % 1000 == 0:
                logger.info(f"Validated {processed_count} URLs")

        logger.info(f"Stage 2 validation completed: {processed_count} URLs processed")

    async def run_concurrent_stage3_enrichment(self, enricher, scrapy_settings):
        """Run Stage 3 enrichment with concurrent queue processing"""
        import asyncio
        import multiprocessing
        from twisted.internet import asyncioreactor
        from scrapy.crawler import CrawlerProcess
        from concurrent.futures import ThreadPoolExecutor
        import threading

        logger.info("Starting concurrent Stage 3 enrichment")

        # Create queue population task
        population_task = asyncio.create_task(self.populate_stage3_queue())

        # Prepare validation data from queue for spider
        validation_items_for_enrichment = []

        async def collect_urls_from_queue():
            """Collect URLs from stage2_to_stage3_queue"""
            count = 0
            while True:
                try:
                    # Use timeout to avoid blocking forever
                    batch = await self.stage2_to_stage3_queue.get_batch_or_wait(timeout=2.0)

                    if not batch:
                        break  # No more items and producer is done

                    # Extract validation data from batch items (preserve url_hash and metadata)
                    for item in batch:
                        validation_items_for_enrichment.append(item.data)
                        count += 1

                        if count % 1000 == 0:
                            logger.info(f"Collected {count} URLs for enrichment")

                except Exception as e:
                    logger.error(f"Error collecting URLs from queue: {e}")
                    break

            logger.info(f"Finished collecting {count} URLs for enrichment")
            return count

        # Wait for population to complete and collect URLs
        await asyncio.gather(population_task, collect_urls_from_queue())

        if not validation_items_for_enrichment:
            logger.warning("No URLs available for Stage 3 enrichment")
            return

        # Run Scrapy in subprocess to avoid reactor conflicts
        import subprocess
        import tempfile
        import json

        # Create temporary file with URLs for the spider
        urls_for_spider = [item.get('url', '') for item in validation_items_for_enrichment if item.get('url')]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(urls_for_spider, f)
            urls_file = f.name

        try:
            # Run Scrapy spider in subprocess to avoid reactor conflicts
            cmd = [
                'scrapy', 'crawl', enricher.name,
                '-s', f'STAGE3_OUTPUT_FILE={scrapy_settings.get("STAGE3_OUTPUT_FILE", "")}',
                '-s', f'LOG_LEVEL={scrapy_settings.get("LOG_LEVEL", "INFO")}',
                '-a', f'urls_file={urls_file}'
            ]

            logger.info(f"Running Scrapy command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(Path(__file__).parent.parent.parent))

            if result.returncode == 0:
                logger.info("Scrapy enrichment completed successfully")
            else:
                logger.error(f"Scrapy process failed with return code {result.returncode}")
                logger.error(f"STDOUT: {result.stdout}")
                logger.error(f"STDERR: {result.stderr}")

        finally:
            # Clean up temporary file
            import os
            if os.path.exists(urls_file):
                os.unlink(urls_file)

        logger.info("Stage 3 enrichment completed")