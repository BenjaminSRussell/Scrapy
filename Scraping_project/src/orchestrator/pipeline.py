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
        self._last_logged_threshold = None  # Track last logged threshold to avoid spam

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
        """Add item to queue with backpressure monitoring (with hysteresis to reduce log spam)"""
        queue = self._get_queue()

        current_size = queue.qsize()

        # Use thresholded logging with hysteresis to avoid spam
        # Only log when crossing 80%, 90%, 95%, or 100% thresholds
        if queue.full():
            if self._last_logged_threshold != 100:
                logger.warning(f"Queue is FULL ({self.max_queue_size}), applying backpressure - producer blocked")
                self._last_logged_threshold = 100
        elif current_size > self.max_queue_size * 0.95:
            if self._last_logged_threshold != 95:
                logger.warning(f"Queue at 95%+ capacity: {current_size}/{self.max_queue_size} items")
                self._last_logged_threshold = 95
        elif current_size > self.max_queue_size * 0.90:
            if self._last_logged_threshold != 90:
                logger.info(f"Queue at 90%+ capacity: {current_size}/{self.max_queue_size} items")
                self._last_logged_threshold = 90
        elif current_size > self.max_queue_size * 0.80:
            if self._last_logged_threshold != 80:
                logger.info(f"Queue at 80%+ capacity: {current_size}/{self.max_queue_size} items")
                self._last_logged_threshold = 80
        elif current_size < self.max_queue_size * 0.70:
            # Reset threshold when queue drains below 70%
            if self._last_logged_threshold is not None:
                logger.info(f"Queue drained to {current_size}/{self.max_queue_size} items (< 70%)")
                self._last_logged_threshold = None

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
        batch_id = 0

        while True:
            batch = await queue.get_batch_or_wait(timeout=2.0)

            if not batch:
                break

            await validator.validate_batch(batch, batch_id=batch_id)
            processed_count += len(batch)
            batch_id += 1

            # Log progress less frequently (every 5000 instead of 1000)
            if processed_count % 5000 == 0:
                logger.info(f"Validated {processed_count} URLs ({batch_id} batches)")

        logger.info(f"Stage 2 validation completed: {processed_count} URLs processed in {batch_id} batches")
    async def run_concurrent_stage3_enrichment(
        self,
        spider_cls,
        scrapy_settings,
        spider_kwargs: dict[str, Any] | None = None,
        crawler_process_factory=None,
        use_async_processor: bool = False,
    ):
        """Run Stage 3 enrichment with concurrent queue processing.

        Args:
            spider_cls: Spider class (used for Scrapy mode)
            scrapy_settings: Scrapy settings dict
            spider_kwargs: Additional spider kwargs
            crawler_process_factory: Factory for creating CrawlerProcess
            use_async_processor: If True, use async processor instead of Scrapy
        """
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

        logger.info(f"Dispatching {len(deduped_urls)} URLs to Stage 3 enrichment")

        # Use async processor if enabled
        if use_async_processor:
            await self._run_async_enrichment(deduped_urls, scrapy_settings, spider_kwargs)
        else:
            await self._run_scrapy_enrichment(
                deduped_urls,
                spider_cls,
                scrapy_settings,
                spider_kwargs,
                validation_items_for_enrichment,
                crawler_process_factory
            )

        logger.info("Stage 3 enrichment completed")

    async def _run_async_enrichment(
        self,
        urls: list[str],
        scrapy_settings: dict,
        spider_kwargs: dict[str, Any] | None
    ):
        """Run enrichment using async processor (faster, concurrent)"""
        from src.stage3.async_enrichment import run_async_enrichment

        stage3_config = self.config.get_stage3_config()
        nlp_config = self.config.get_nlp_config()
        content_config = self.config.get('content', default={})

        output_file = stage3_config[keys.ENRICHMENT_OUTPUT_FILE]
        content_types_config = stage3_config.get(keys.ENRICHMENT_CONTENT_TYPES, {})
        storage_config = stage3_config.get(keys.ENRICHMENT_STORAGE, {})
        predefined_tags = content_config.get('predefined_tags', [])
        max_workers = stage3_config.get(keys.VALIDATION_MAX_WORKERS, 50)

        logger.info(f"Using AsyncEnrichmentProcessor with max_concurrency={max_workers}")

        await run_async_enrichment(
            urls=urls,
            output_file=output_file,
            nlp_config=nlp_config,
            content_types_config=content_types_config,
            predefined_tags=predefined_tags,
            max_concurrency=max_workers,
            timeout=30,
            batch_size=100,
            storage_config=storage_config,
            storage_backend=storage_config.get(keys.STORAGE_BACKEND),
            storage_options=storage_config.get(keys.STORAGE_OPTIONS, {}),
            rotation_config=storage_config.get(keys.STORAGE_ROTATION, {}),
            compression_config=storage_config.get(keys.STORAGE_COMPRESSION, {})
        )

    async def _run_scrapy_enrichment(
        self,
        urls: list[str],
        spider_cls,
        scrapy_settings: dict,
        spider_kwargs: dict[str, Any] | None,
        validation_items: list[dict[str, Any]],
        crawler_process_factory,
    ):
        """Run enrichment using Scrapy (traditional, single-threaded)"""
        spider_kwargs = dict(spider_kwargs or {})

        existing_urls = list(spider_kwargs.get('urls_list', []))
        combined_urls = list(existing_urls)
        seen_urls = set(existing_urls)
        for url in urls:
            if url not in seen_urls:
                combined_urls.append(url)
                seen_urls.add(url)
        spider_kwargs['urls_list'] = combined_urls

        existing_metadata = list(spider_kwargs.get('validation_metadata', []))
        combined_metadata = existing_metadata + validation_items
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
            import sys
            try:
                process.crawl(spider_cls, **spider_kwargs)
                process.start()  # This blocks until crawling is done
            except KeyboardInterrupt:
                logger.info("Crawler interrupted by user")
            except Exception as e:
                logger.error(f"Crawler error: {e}", exc_info=True)
                # Re-raise to propagate to executor
                raise
            finally:
                # Clean shutdown - Scrapy already handles this internally
                # Don't call stop() as it can cause conflicts
                logger.debug("Scrapy crawler finished")

        loop = asyncio.get_running_loop()
        logger.info("Using Scrapy enrichment (traditional mode)")

        try:
            await loop.run_in_executor(None, _run_crawler)
        except asyncio.CancelledError:
            logger.warning("Scrapy enrichment was cancelled")
            raise
        except Exception as e:
            logger.error(f"Scrapy enrichment failed: {e}", exc_info=True)
            raise
