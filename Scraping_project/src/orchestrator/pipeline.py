from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncGenerator
from dataclasses import dataclass
from datetime import datetime
import logging

from src.common import config_keys as keys
from src.orchestrator.orchestrator_validation import validate_stage_output

logger = logging.getLogger(__name__)


@dataclass
class BatchQueueItem:
    """Item in the processing queue"""
    url: str
    url_hash: str
    source_stage: str
    data: dict[str, Any]
    created_at: str = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()


class BatchQueue:
    """Manages batch processing with backpressure rules"""

    def __init__(self, batch_size: int = 1000, max_queue_size: int = 10000):
        self.batch_size = batch_size
        self.max_queue_size = max_queue_size
        self._queue: asyncio.Queue | None = None
        self._running = False
        self._producer_done = False

    def _get_queue(self) -> asyncio.Queue:
        """Return the underlying asyncio queue, creating it lazily."""
        if self._queue is None:
            try:
                asyncio.get_running_loop()
            except RuntimeError as exc:
                raise RuntimeError(
                    "BatchQueue requires an active event loop; call async methods"
                    " from within an asyncio context"
                ) from exc

            self._queue = asyncio.Queue(maxsize=self.max_queue_size)

        return self._queue

    async def put(self, item: BatchQueueItem):
        """Add item to queue with backpressure monitoring"""
        queue = self._get_queue()

        current_size = queue.qsize()
        if queue.full():
            logger.warning(f"Queue is full ({self.max_queue_size}), applying backpressure - producer blocked")
        elif current_size > self.max_queue_size * 0.8:
            logger.info(f"Queue backpressure: {current_size}/{self.max_queue_size} items ({current_size * 100 // self.max_queue_size}% full)")

        await queue.put(item)

    async def get_batch(self) -> list[BatchQueueItem]:
        """Get a batch of items from the queue"""
        queue = self._get_queue()
        batch = []

        try:
            item = await queue.get()
            batch.append(item)
        except asyncio.QueueEmpty:
            return batch

        while len(batch) < self.batch_size:
            try:
                item = queue.get_nowait()
                batch.append(item)
            except asyncio.QueueEmpty:
                break

        return batch

    def qsize(self) -> int:
        """Get current queue size"""
        return self._queue.qsize() if self._queue else 0

    def is_empty(self) -> bool:
        """Check if queue is empty"""
        return True if self._queue is None else self._queue.empty()

    def is_full(self) -> bool:
        """Check if queue is full"""
        return False if self._queue is None else self._queue.full()

    def mark_producer_done(self):
        """Mark that no more items will be added to the queue"""
        self._producer_done = True

    def is_producer_done(self) -> bool:
        """Check if producer is finished adding items"""
        return self._producer_done

    async def get_batch_or_wait(self, timeout: float = 1.0) -> list[BatchQueueItem]:
        """Get a batch with timeout, handling producer completion"""
        batch = []
        queue = self._get_queue()

        try:
            item = await asyncio.wait_for(queue.get(), timeout=timeout)
            batch.append(item)

            while len(batch) < self.batch_size:
                try:
                    item = queue.get_nowait()
                    batch.append(item)
                except asyncio.QueueEmpty:
                    break

        except asyncio.TimeoutError:
            if self._producer_done and queue.empty():
                return []
            pass

        return batch


class PipelineOrchestrator:
    """Orchestrates the multi-stage pipeline with batch processing"""

    def __init__(self, config):
        self.config = config
        self.stage1_to_stage2_queue = BatchQueue(
            batch_size=config.get_stage2_config()[keys.VALIDATION_MAX_WORKERS] or 1000
        )
        self.stage2_to_stage3_queue = BatchQueue(
            batch_size=config.get_stage3_config().get(keys.ENRICHMENT_BATCH_SIZE, 1000)
        )

    async def load_stage1_results(self) -> AsyncGenerator[BatchQueueItem, None]:
        """Load Stage 1 discovery results and yield as queue items"""
        stage1_config = self.config.get_stage1_config()
        output_file = Path(stage1_config[keys.DISCOVERY_OUTPUT_FILE])

        if not output_file.exists():
            logger.warning(f"Stage 1 output file not found: {output_file}")
            return

        validate_stage_output(1, output_file)

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
        output_file = Path(stage2_config[keys.VALIDATION_OUTPUT_FILE])

        if not output_file.exists():
            logger.warning(f"Stage 2 output file not found: {output_file}")
            return

        validate_stage_output(2, output_file)

        logger.info(f"Loading Stage 2 results from {output_file}")

        with open(output_file, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())

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
        """Populate the Stage 2 queue from Stage 1 results"""
        logger.info("Populating Stage 2 queue from Stage 1 results")

        count = 0
        try:
            async for item in self.load_stage1_results():
                await self.stage1_to_stage2_queue.put(item)
                count += 1

                if count % 1000 == 0:
                    logger.info(f"Queued {count} items for Stage 2")
        finally:
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
            self.stage2_to_stage3_queue.mark_producer_done()
            logger.info(f"Finished queuing {count} items for Stage 3")

    def get_stage2_queue(self) -> BatchQueue:
        """Get the Stage 2 processing queue"""
        return self.stage1_to_stage2_queue

    def get_stage3_queue(self) -> BatchQueue:
        """Get the Stage 3 processing queue"""
        return self.stage2_to_stage3_queue

    async def run_concurrent_stage2_validation(self, validator):
        """Run Stage 2 validation with concurrent population and consumption"""
        logger.info("Starting concurrent Stage 2 validation")

        producer_task = asyncio.create_task(self.populate_stage2_queue())
        consumer_task = asyncio.create_task(self._consume_stage2_queue(validator))

        await asyncio.gather(producer_task, consumer_task)

    async def _consume_stage2_queue(self, validator):
        """Consumer for Stage 2 queue - processes batches as they become available"""
        queue = self.get_stage2_queue()
        processed_count = 0

        while True:
            batch = await queue.get_batch_or_wait(timeout=2.0)

            if not batch:
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

        population_task = asyncio.create_task(self.populate_stage3_queue())

        validation_items_for_enrichment = []

        async def collect_urls_from_queue():
            """Collect URLs from stage2_to_stage3_queue"""
            count = 0
            while True:
                try:
                    batch = await self.stage2_to_stage3_queue.get_batch_or_wait(timeout=2.0)

                    if not batch:
                        break

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

        await asyncio.gather(population_task, collect_urls_from_queue())

        if not validation_items_for_enrichment:
            logger.warning("No URLs available for Stage 3 enrichment")
            return

        import subprocess
        import json
        from datetime import datetime
        from pathlib import Path

        data_paths = self.config.get_data_paths()
        temp_dir = data_paths.get(keys.TEMP_DIR, Path("data/temp"))
        temp_dir.mkdir(parents=True, exist_ok=True)

        urls_for_enrichment = [item.get('url', '') for item in validation_items_for_enrichment if item.get('url')]

        urls_file = temp_dir / f"enrichment_urls_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(urls_file, 'w') as f:
            json.dump(urls_for_enrichment, f)
        urls_file = str(urls_file)

        try:
            stage3_config = self.config.get_stage3_config()
            spider_name = stage3_config.get(keys.ENRICHMENT_SPIDER_NAME, 'enrichment')

            cmd = [
                'scrapy', 'crawl', spider_name,
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
            if Path(urls_file).exists():
                Path(urls_file).unlink()

        logger.info("Stage 3 enrichment completed")