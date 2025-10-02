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

        logger.info(f"Stage 2 validation completed: {processed_count} URLs processed"
    async def run_concurrent_stage3_enrichment(
        self,
        spider_cls,
        scrapy_settings,
        spider_kwargs: dict[str, Any] | None = None,
        crawler_process_factory=None,
    ):
        """Run Stage 3 enrichment with concurrent queue processing."""
        logger.info("Starting concurrent Stage 3 enrichment")

        population_task = asyncio.create_task(self.populate_stage3_queue())
        validation_items_for_enrichment: list[dict[str, Any]] = []

        async def collect_urls_from_queue() -> int:
            count = 0
            while True:
                batch = await self.stage2_to_stage3_queue.get_batch_or_wait(timeout=2.0)

                if not batch:
                    break

                for item in batch:
                    validation_items_for_enrichment.append(item.data)
                    count += 1

                    if count % 1000 == 0:
                        logger.info(f"Collected {count} URLs for enrichment")

            logger.info(f"Finished collecting {count} URLs for enrichment")
            return count

        await asyncio.gather(population_task, collect_urls_from_queue())

        if not validation_items_for_enrichment:
            logger.warning("No URLs available for Stage 3 enrichment")
            return

        urls_for_enrichment: list[str] = []
        for item in validation_items_for_enrichment:
            url = item.get('url')
            if url:
                urls_for_enrichment.append(url)

        if not urls_for_enrichment:
            logger.warning("No valid URLs found for Stage 3 enrichment")
            return

        seen_urls: set[str] = set()
        deduped_urls: list[str] = []
        for url in urls_for_enrichment:
            if url not in seen_urls:
                seen_urls.add(url)
                deduped_urls.append(url)

        spider_kwargs = dict(spider_kwargs or {})

        existing_urls = list(spider_kwargs.get('urls_list', []))
        combined_urls = existing_urls + [url for url in deduped_urls if url not in existing_urls]
        spider_kwargs['urls_list'] = combined_urls

        existing_metadata = list(spider_kwargs.get('validation_metadata', []))
        combined_metadata = existing_metadata + validation_items_for_enrichment
        metadata_by_url: dict[str, Any] = {}
        for entry in combined_metadata:
            url = entry.get('url')
            if url:
                metadata_by_url[url] = entry
        spider_kwargs['validation_metadata'] = list(metadata_by_url.values())

        if crawler_process_factory is None:
            from scrapy.crawler import CrawlerProcess

            crawler_process_factory = CrawlerProcess

        process = crawler_process_factory(scrapy_settings)

        def _run_crawler() -> None:
            try:
                process.crawl(spider_cls, **spider_kwargs)
                process.start()
            finally:
                stop = getattr(process, 'stop', None)
                if callable(stop):
                    try:
                        stop()
                    except Exception:
                        logger.debug("CrawlerProcess.stop raised", exc_info=True)

        loop = asyncio.get_running_loop()
        logger.info(f"Dispatching {len(deduped_urls)} URLs to Stage 3 crawler")

        await loop.run_in_executor(None, _run_crawler)

        logger.info("Stage 3 enrichment completed")
