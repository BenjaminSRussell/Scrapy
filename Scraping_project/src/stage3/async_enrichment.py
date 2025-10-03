"""
Asynchronous enrichment processor for Stage 3.

Provides high-performance concurrent URL fetching and content processing
using asyncio, aiohttp, and adaptive concurrency control.
"""

import asyncio
import aiohttp
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, asdict
from urllib.parse import urlparse
from collections import deque
import time

from .storage import create_storage_writer
from src.common.nlp import (
    extract_entities_and_keywords,
    extract_content_tags,
    has_audio_links,
    summarize,
    initialize_nlp,
    NLPSettings
)
from src.common.content_handlers import ContentTypeRouter
from src.common.checkpoint_middleware import AsyncCheckpointTracker

logger = logging.getLogger(__name__)


@dataclass
class EnrichmentResult:
    """Result of enriching a single URL"""
    url: str
    url_hash: str
    title: str = ""
    text_content: str = ""
    word_count: int = 0
    entities: List[Dict[str, Any]] = None
    keywords: List[Dict[str, float]] = None
    content_tags: List[str] = None
    has_pdf_links: bool = False
    has_audio_links: bool = False
    status_code: int = 0
    content_type: str = ""
    enriched_at: str = ""
    content_summary: str = ""
    error: Optional[str] = None
    fetch_duration_ms: float = 0.0
    process_duration_ms: float = 0.0

    def __post_init__(self):
        if self.entities is None:
            self.entities = []
        if self.keywords is None:
            self.keywords = []
        if self.content_tags is None:
            self.content_tags = []
        if not self.enriched_at:
            self.enriched_at = datetime.now().isoformat()
        if not self.url_hash:
            self.url_hash = hashlib.sha256(self.url.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


class AdaptiveConcurrencyController:
    """
    Adaptive concurrency control using additive increase / multiplicative decrease (AIMD).

    Automatically adjusts concurrency based on success/failure rates and response times.
    """

    def __init__(
        self,
        initial_concurrency: int = 10,
        min_concurrency: int = 2,
        max_concurrency: int = 100,
        increase_interval: float = 5.0,
        target_success_rate: float = 0.95
    ):
        self.current = initial_concurrency
        self.min = min_concurrency
        self.max = max_concurrency
        self.increase_interval = increase_interval
        self.target_success_rate = target_success_rate

        self._last_increase_time = time.time()
        self._recent_requests = deque(maxlen=100)  # Track last 100 requests
        self._semaphore = asyncio.Semaphore(initial_concurrency)

    def record_request(self, success: bool, duration_ms: float):
        """Record request result for adaptive adjustment"""
        self._recent_requests.append({
            'success': success,
            'duration_ms': duration_ms,
            'timestamp': time.time()
        })

    def get_success_rate(self) -> float:
        """Calculate recent success rate"""
        if not self._recent_requests:
            return 1.0

        successful = sum(1 for r in self._recent_requests if r['success'])
        return successful / len(self._recent_requests)

    def get_avg_duration_ms(self) -> float:
        """Calculate average response time"""
        if not self._recent_requests:
            return 0.0

        total = sum(r['duration_ms'] for r in self._recent_requests)
        return total / len(self._recent_requests)

    def adjust_concurrency(self):
        """Adjust concurrency based on recent performance"""
        current_time = time.time()
        success_rate = self.get_success_rate()

        # Decrease on poor performance (multiplicative decrease)
        if success_rate < self.target_success_rate:
            old_value = self.current
            self.current = max(self.min, int(self.current * 0.5))
            if old_value != self.current:
                logger.info(
                    f"Decreasing concurrency: {old_value} -> {self.current} "
                    f"(success rate: {success_rate:.2%})"
                )
                # Recreate semaphore with new limit
                self._semaphore = asyncio.Semaphore(self.current)
            return

        # Increase on good performance (additive increase)
        if current_time - self._last_increase_time >= self.increase_interval:
            if success_rate >= self.target_success_rate and self.current < self.max:
                old_value = self.current
                self.current = min(self.max, self.current + 2)
                if old_value != self.current:
                    logger.info(
                        f"Increasing concurrency: {old_value} -> {self.current} "
                        f"(success rate: {success_rate:.2%})"
                    )
                    # Recreate semaphore with new limit
                    self._semaphore = asyncio.Semaphore(self.current)
                self._last_increase_time = current_time

    async def acquire(self):
        """Acquire semaphore slot"""
        await self._semaphore.acquire()

    def release(self):
        """Release semaphore slot"""
        self._semaphore.release()

    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        return {
            'current_concurrency': self.current,
            'success_rate': self.get_success_rate(),
            'avg_duration_ms': self.get_avg_duration_ms(),
            'recent_requests': len(self._recent_requests)
        }


class AsyncEnrichmentProcessor:
    """
    High-performance async enrichment processor.

    Features:
    - Concurrent URL fetching with connection pooling
    - Adaptive concurrency control
    - Async NLP processing
    - Progress tracking and statistics
    """

    def __init__(
        self,
        output_file: str,
        nlp_config: Optional[Dict[str, Any]] = None,
        content_types_config: Optional[Dict[str, Any]] = None,
        predefined_tags: Optional[List[str]] = None,
        max_concurrency: int = 50,
        timeout: int = 30,
        max_retries: int = 2,
        batch_size: int = 100,
        storage_config: Optional[Dict[str, Any]] = None,
        storage_backend: Optional[str] = None,
        storage_options: Optional[Dict[str, Any]] = None,
        rotation_config: Optional[Dict[str, Any]] = None,
        compression_config: Optional[Dict[str, Any]] = None,
    ):
        self.output_path = Path(output_file) if output_file else Path("data/processed/stage03/enrichment_output.jsonl")
        self.output_file = self.output_path  # Add alias for backward compatibility
        self.nlp_config = nlp_config or {}
        self.content_types_config = content_types_config or {}
        self.predefined_tags = set(predefined_tags or [])
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.batch_size = batch_size

        base_config: Dict[str, Any] = dict(storage_config or {})
        if storage_backend:
            base_config["backend"] = storage_backend
        if storage_options:
            options = dict(base_config.get("options") or {})
            options.update(storage_options)
            base_config["options"] = options
        if rotation_config is not None:
            base_config["rotation"] = rotation_config
        if compression_config is not None:
            base_config["compression"] = compression_config

        self.storage_config = base_config
        self.active_backend = (base_config.get("backend") or "jsonl").lower()
        self.storage_writer = None

        # Initialize NLP
        if nlp_config:
            nlp_settings = NLPSettings(
                spacy_model=nlp_config.get('spacy_model', 'en_core_web_sm'),
                transformer_model=nlp_config.get('transformer_ner_model') if nlp_config.get('use_transformers') else None,
                summarizer_model=nlp_config.get('summarizer_model') if nlp_config.get('use_transformers') else None,
                zero_shot_model=nlp_config.get('zero_shot_model') if nlp_config.get('use_transformers') else None,
                preferred_device=nlp_config.get('device', 'auto')
            )
            initialize_nlp(nlp_settings)
            self.use_transformer_ner = nlp_config.get('use_transformers', False)
            # Ensure lengths are integers for slicing operations
            max_len = nlp_config.get('summary_max_length', 150)
            min_len = nlp_config.get('summary_min_length', 30)
            self.summary_max_length = int(max_len) if max_len is not None else 150
            self.summary_min_length = int(min_len) if min_len is not None else 30
        else:
            self.use_transformer_ner = False
            self.summary_max_length = 150
            self.summary_min_length = 30

        # Content type router
        if self.content_types_config:
            self.content_router = ContentTypeRouter(self.content_types_config)
        else:
            self.content_router = None

        # Adaptive concurrency controller
        self.concurrency_controller = AdaptiveConcurrencyController(
            initial_concurrency=min(10, max_concurrency),
            min_concurrency=2,
            max_concurrency=max_concurrency
        )

        # Statistics
        self.stats = {
            'total_processed': 0,
            'total_success': 0,
            'total_failed': 0,
            'total_fetch_time_ms': 0.0,
            'total_process_time_ms': 0.0,
            'start_time': None,
            'end_time': None
        }

        self._output_lock = asyncio.Lock()

        # Checkpoint tracker
        checkpoint_dir = Path('data/checkpoints')
        self.checkpoint_tracker = AsyncCheckpointTracker(checkpoint_dir, 'stage3_async_enrichment')


    def _build_writer(self):
        config = dict(self.storage_config)
        backend = (config.get('backend') or 'jsonl').lower()
        self.active_backend = backend
        options = dict(config.get('options') or {})
        rotation = config.get('rotation')
        compression = config.get('compression')

        default_path = self.output_path
        if backend != 's3' and 'path' not in options:
            options['path'] = str(default_path)

        return create_storage_writer(
            backend=backend,
            options=options,
            rotation_config=rotation,
            compression_config=compression,
            default_path=default_path,
        )

    async def __aenter__(self):

        """Async context manager entry"""

        try:

            self.storage_writer = self._build_writer()

            self.storage_writer.open()

        except Exception:  # pragma: no cover - defensive logging

            logger.error("AsyncEnrichmentProcessor failed to initialize storage backend", exc_info=True)

            raise



        self.stats['start_time'] = time.time()

        logger.info("AsyncEnrichmentProcessor initialized with %s backend -> %s",

                    self.active_backend.upper(),

                    self.storage_writer.describe_destination())



        # Initialize checkpoint tracker

        await self.checkpoint_tracker.__aenter__()



        return self





    async def __aexit__(self, exc_type, exc_val, exc_tb):

        """Async context manager exit"""

        if self.storage_writer:

            self.storage_writer.close()

            self.storage_writer = None



        self.stats['end_time'] = time.time()

        self._log_final_stats()



        # Clean up checkpoint tracker

        await self.checkpoint_tracker.__aexit__(exc_type, exc_val, exc_tb)





    async def _write_result(self, result: EnrichmentResult):

        """Write result to storage (thread-safe)"""

        async with self._output_lock:

            try:

                if not self.storage_writer:

                    raise RuntimeError('Storage writer is not initialized')

                self.storage_writer.write_item(result.to_dict())

            except Exception as e:

                logger.error(f"Error writing result for {result.url}: {e}")





    async def fetch_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
        url_hash: str
    ) -> EnrichmentResult:
        """Fetch and enrich a single URL"""
        start_time = time.time()
        fetch_start = start_time

        # Acquire concurrency slot
        await self.concurrency_controller.acquire()

        try:
            # Fetch with retries
            response_data = None
            last_error = None

            for attempt in range(self.max_retries + 1):
                try:
                    async with session.get(url, timeout=self.timeout) as response:
                        fetch_duration_ms = (time.time() - fetch_start) * 1000

                        content_type = response.headers.get('Content-Type', '')
                        normalized_content_type = content_type.split(';')[0].strip().lower()

                        response_data = {
                            'body': await response.read(),
                            'text': await response.text(errors='ignore') if normalized_content_type.startswith('text/') else '',
                            'status': response.status,
                            'content_type': content_type,
                            'normalized_content_type': normalized_content_type
                        }

                        # Record successful request
                        self.concurrency_controller.record_request(True, fetch_duration_ms)
                        break

                except Exception as e:
                    last_error = str(e)
                    fetch_duration_ms = (time.time() - fetch_start) * 1000
                    self.concurrency_controller.record_request(False, fetch_duration_ms)

                    if attempt < self.max_retries:
                        await asyncio.sleep(1 * (attempt + 1))  # Exponential backoff
                        continue
                    else:
                        # All retries exhausted
                        return EnrichmentResult(
                            url=url,
                            url_hash=url_hash,
                            error=f"Fetch failed after {self.max_retries + 1} attempts: {last_error}",
                            fetch_duration_ms=fetch_duration_ms
                        )

            if not response_data:
                return EnrichmentResult(
                    url=url,
                    url_hash=url_hash,
                    error="No response data",
                    fetch_duration_ms=fetch_duration_ms
                )

            # Process response
            process_start = time.time()
            result = await self._process_response(url, url_hash, response_data)
            process_duration_ms = (time.time() - process_start) * 1000

            result.fetch_duration_ms = fetch_duration_ms
            result.process_duration_ms = process_duration_ms

            # Adjust concurrency based on performance
            self.concurrency_controller.adjust_concurrency()

            return result

        finally:
            self.concurrency_controller.release()

    async def _process_response(
        self,
        url: str,
        url_hash: str,
        response_data: Dict[str, Any]
    ) -> EnrichmentResult:
        """Process HTTP response and extract content"""
        from lxml import html as lxml_html

        try:
            normalized_content_type = response_data['normalized_content_type']

            # Handle non-HTML content (PDF, images, etc.)
            if self.content_router and normalized_content_type != 'text/html':
                if self.content_router.can_process(normalized_content_type):
                    content_data = self.content_router.process_content(
                        response_data['body'],
                        url,
                        url_hash,
                        normalized_content_type
                    )

                    return EnrichmentResult(
                        url=url,
                        url_hash=url_hash,
                        title=content_data.get('metadata', {}).get('title', ''),
                        text_content=content_data.get('text_content', ''),
                        content_summary=content_data.get('text_content', '')[:500] if content_data.get('text_content') else '',
                        entities=[],
                        keywords=[],
                        content_tags=[],
                        has_pdf_links=False,
                        has_audio_links=False,
                        status_code=response_data['status'],
                        content_type=response_data['content_type'],
                        word_count=content_data.get('word_count', 0)
                    )

            # HTML content processing
            text = response_data['text']
            if not text:
                return EnrichmentResult(
                    url=url,
                    url_hash=url_hash,
                    error="Empty response body",
                    status_code=response_data['status'],
                    content_type=response_data['content_type']
                )

            # Parse HTML
            tree = lxml_html.fromstring(text)

            # Extract title
            title_elements = tree.xpath('//title/text()')
            title = (title_elements[0].strip() if title_elements else '') or ''

            # Extract body text (excluding scripts, styles)
            text_elements = tree.xpath('//body//text()[normalize-space() and not(ancestor::script) and not(ancestor::style)]')
            text_content = ' '.join(text_elements).strip()

            # NLP analysis (run in executor to avoid blocking)
            loop = asyncio.get_event_loop()
            backend = "transformer" if self.use_transformer_ner else "spacy"

            entities, keywords = await loop.run_in_executor(
                None,
                lambda: extract_entities_and_keywords(text_content, backend=backend)
            )

            content_summary = await loop.run_in_executor(
                None,
                summarize,
                text_content,
                self.summary_max_length,
                self.summary_min_length
            )

            # Extract content tags
            url_path = urlparse(url).path
            content_tags = extract_content_tags(url_path, self.predefined_tags)

            # Extract links
            link_elements = tree.xpath('//a/@href')
            links = [str(link) for link in link_elements]

            has_pdf_links = any('pdf' in link.lower() for link in links)
            has_audio = has_audio_links(links) or bool(tree.xpath('//audio'))

            return EnrichmentResult(
                url=url,
                url_hash=url_hash,
                title=title,
                text_content=text_content[:20000],  # Limit text length
                word_count=len(text_content.split()) if text_content else 0,
                entities=entities,
                keywords=keywords,
                content_tags=content_tags,
                has_pdf_links=has_pdf_links,
                has_audio_links=has_audio,
                status_code=response_data['status'],
                content_type=response_data['content_type'],
                content_summary=content_summary
            )

        except Exception as e:
            logger.error(f"Error processing response for {url}: {e}", exc_info=True)
            return EnrichmentResult(
                url=url,
                url_hash=url_hash,
                error=f"Processing error: {str(e)}",
                status_code=response_data.get('status', 0),
                content_type=response_data.get('content_type', '')
            )

    async def process_batch(
        self,
        session: aiohttp.ClientSession,
        urls: List[str]
    ):
        """Process a batch of URLs concurrently"""
        tasks = []
        for url in urls:
            url_hash = hashlib.sha256(url.encode()).hexdigest()
            task = self.fetch_url(session, url, url_hash)
            tasks.append(task)

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Write results and update stats
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Task exception: {result}")
                self.stats['total_failed'] += 1
                continue

            if isinstance(result, EnrichmentResult):
                await self._write_result(result)

                self.stats['total_processed'] += 1
                if result.error:
                    self.stats['total_failed'] += 1
                else:
                    self.stats['total_success'] += 1

                self.stats['total_fetch_time_ms'] += result.fetch_duration_ms
                self.stats['total_process_time_ms'] += result.process_duration_ms

                # Update checkpoint
                self.checkpoint_tracker.update(
                    processed=1,
                    successful=0 if result.error else 1,
                    failed=1 if result.error else 0,
                    last_item=result.url,
                    index=self.stats['total_processed']
                )

                # Log progress
                if self.stats['total_processed'] % 100 == 0:
                    self._log_progress()
                    self.checkpoint_tracker.print_progress()

    async def process_urls(self, urls: List[str]):
        """Process list of URLs with batching and connection pooling"""
        if not urls:
            logger.warning("No URLs to process")
            return

        logger.info(f"Processing {len(urls)} URLs with adaptive concurrency")

        # Initialize checkpoint with total count
        self.checkpoint_tracker.start(total_items=len(urls))

        # Check if we should resume
        resume_point = self.checkpoint_tracker.get_resume_point()
        if resume_point['status'] == 'recovering':
            logger.info(f"Resuming from {resume_point['processed_items']} items")

        # Create connection pool
        connector = aiohttp.TCPConnector(
            limit=self.concurrency_controller.max,
            limit_per_host=20,
            ttl_dns_cache=300
        )

        try:
            async with aiohttp.ClientSession(connector=connector) as session:
                # Process in batches
                for i in range(0, len(urls), self.batch_size):
                    # Skip already processed batches if resuming (only in recovering mode)
                    if resume_point['status'] == 'recovering' and self.checkpoint_tracker.should_skip(i):
                        continue

                    batch = urls[i:i + self.batch_size]
                    await self.process_batch(session, batch)

            # Mark as completed only if we finished successfully
            self.checkpoint_tracker.complete()
            self._log_final_stats()

        except asyncio.CancelledError:
            logger.warning("Async enrichment cancelled by user")
            self.checkpoint_tracker.fail("Cancelled by user")
            raise
        except Exception as e:
            logger.error(f"Async enrichment failed: {e}", exc_info=True)
            self.checkpoint_tracker.fail(str(e))
            raise

    def _log_progress(self):
        """Log current progress"""
        concurrency_stats = self.concurrency_controller.get_stats()
        success_rate = (self.stats['total_success'] / self.stats['total_processed'] * 100
                       if self.stats['total_processed'] > 0 else 0)

        logger.info(
            f"Processed: {self.stats['total_processed']} | "
            f"Success: {success_rate:.1f}% | "
            f"Concurrency: {concurrency_stats['current_concurrency']} | "
            f"Avg fetch: {concurrency_stats['avg_duration_ms']:.0f}ms"
        )

    def _log_final_stats(self):
        """Log final statistics"""
        if not self.stats['start_time']:
            return

        duration = (self.stats['end_time'] or time.time()) - self.stats['start_time']
        avg_fetch_ms = (self.stats['total_fetch_time_ms'] / self.stats['total_processed']
                       if self.stats['total_processed'] > 0 else 0)
        avg_process_ms = (self.stats['total_process_time_ms'] / self.stats['total_processed']
                         if self.stats['total_processed'] > 0 else 0)
        throughput = self.stats['total_processed'] / duration if duration > 0 else 0

        logger.info("=" * 80)
        logger.info("Async Enrichment Processor - Final Statistics")
        logger.info("=" * 80)
        logger.info(f"Total processed: {self.stats['total_processed']}")
        logger.info(f"Success: {self.stats['total_success']}")
        logger.info(f"Failed: {self.stats['total_failed']}")
        logger.info(f"Duration: {duration:.1f}s")
        logger.info(f"Throughput: {throughput:.1f} URLs/sec")
        logger.info(f"Avg fetch time: {avg_fetch_ms:.0f}ms")
        logger.info(f"Avg process time: {avg_process_ms:.0f}ms")
        logger.info(f"Final concurrency: {self.concurrency_controller.current}")
        logger.info("=" * 80)


async def run_async_enrichment(
    urls: List[str],
    output_file: str,
    nlp_config: Optional[Dict[str, Any]] = None,
    content_types_config: Optional[Dict[str, Any]] = None,
    predefined_tags: Optional[List[str]] = None,
    max_concurrency: int = 50,
    timeout: int = 30,
    batch_size: int = 100,
    storage_config: Optional[Dict[str, Any]] = None,
    storage_backend: Optional[str] = None,
    storage_options: Optional[Dict[str, Any]] = None,
    rotation_config: Optional[Dict[str, Any]] = None,
    compression_config: Optional[Dict[str, Any]] = None
):
    """
    Convenience function to run async enrichment.

    Args:
        urls: List of URLs to process
        output_file: Path to output JSONL file
        nlp_config: NLP configuration dict
        content_types_config: Content types configuration dict
        predefined_tags: List of predefined content tags
        max_concurrency: Maximum concurrent requests
        timeout: Request timeout in seconds
        batch_size: Batch size for processing
        storage_config: Storage backend configuration dictionary
        storage_backend: Optional override for the storage backend name
        storage_options: Optional override for backend-specific options
        rotation_config: Optional rotation policy configuration
        compression_config: Optional compression settings
    """
    async with AsyncEnrichmentProcessor(
        output_file=output_file,
        nlp_config=nlp_config,
        content_types_config=content_types_config,
        predefined_tags=predefined_tags,
        max_concurrency=max_concurrency,
        timeout=timeout,
        batch_size=batch_size,
        storage_config=storage_config,
        storage_backend=storage_backend,
        storage_options=storage_options,
        rotation_config=rotation_config,
        compression_config=compression_config
    ) as processor:
        await processor.process_urls(urls)
