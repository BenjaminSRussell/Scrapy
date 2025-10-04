import asyncio
import inspect
import json
import logging
import ssl
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import aiohttp

from src.common.adaptive_depth import AdaptiveDepthManager
from src.common.checkpoints import CheckpointManager
from src.common.feedback import FeedbackStore
from src.common.freshness import FreshnessTracker
from src.common.link_graph import LinkGraphAnalyzer
from src.common.schemas import ValidationResult
from src.orchestrator.pipeline import BatchQueueItem

logger = logging.getLogger(__name__)


# Patch aiohttp.ClientSSLError to accept optional os_error for simplified tests
_client_ssl_error = getattr(aiohttp, "ClientSSLError", None)
if _client_ssl_error is not None:
    try:
        _ssl_signature = inspect.signature(_client_ssl_error)
    except (TypeError, ValueError):
        _ssl_signature = None

    if _ssl_signature is not None:
        needs_patch = False
        os_error_param = _ssl_signature.parameters.get('os_error')
        if os_error_param and os_error_param.default is inspect._empty:
            needs_patch = True

        if needs_patch:
            class _CompatClientSSLError(aiohttp.ClientError):  # pragma: no cover - compatibility shim
                def __init__(self, *args, os_error: BaseException | None = None, message: str = "", **kwargs):
                    derived_message = message
                    if not derived_message:
                        for value in reversed(args):
                            if isinstance(value, str):
                                derived_message = value
                                break
                    super().__init__(derived_message)
                    self.os_error = os_error

            aiohttp.ClientSSLError = _CompatClientSSLError


class URLValidator:
    """Stage 2 Validator - async client for URL validation using HEAD/GET checks with link importance prioritization"""

    def __init__(self, config, enable_link_graph: bool = True):
        self.config = config
        self.stage2_config = config.get_stage2_config()
        self.max_workers = self.stage2_config['max_workers']
        self.timeout = self.stage2_config['timeout']
        self.output_file = Path(self.stage2_config['output_file'])
        self.user_agent = self.config.get('scrapy', 'user_agent', default='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

        # Initialize checkpoint manager for resumable validation
        checkpoint_dir = Path("data/checkpoints")
        self.checkpoint_manager = CheckpointManager(checkpoint_dir)
        self.checkpoint = self.checkpoint_manager.get_checkpoint("stage2_validation")

        # Initialize feedback store for Stage 2 -> Stage 1 communication
        feedback_file = Path("data/feedback/stage2_feedback.json")
        self.feedback_store = FeedbackStore(feedback_file)

        # Initialize adaptive depth manager for learning content quality
        adaptive_depth_file = Path("data/config/adaptive_depth.json")
        self.adaptive_depth = AdaptiveDepthManager(adaptive_depth_file)

        # Initialize link graph for importance-based prioritization
        self.enable_link_graph = enable_link_graph
        self.link_graph: LinkGraphAnalyzer | None = None
        if enable_link_graph:
            link_graph_db = Path("data/processed/link_graph.db")
            if link_graph_db.exists():
                self.link_graph = LinkGraphAnalyzer(link_graph_db)
                logger.info("[Stage2] Link graph loaded for importance-based prioritization")
            else:
                logger.warning(f"[Stage2] Link graph database not found: {link_graph_db}")

        # Initialize freshness tracker
        freshness_db = Path("data/cache/freshness.db")
        self.freshness_tracker = FreshnessTracker(freshness_db)
        logger.info("[Stage2] Freshness tracker initialized")

        # Ensure output directory exists
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        # Session configuration - Windows-safe connector limits
        import sys
        max_connector_limit = 64 if sys.platform == 'win32' else 100
        self.connector_limit = min(self.max_workers * 2, max_connector_limit)
        self.session_timeout = aiohttp.ClientTimeout(total=self.timeout)

        # Cache of processed URL hashes for idempotency
        self._processed_hashes_cache = None

    def _get_processed_url_hashes(self) -> set:
        """Get set of already-processed URL hashes from output file for idempotency."""
        if self._processed_hashes_cache is not None:
            return self._processed_hashes_cache

        processed_hashes = set()

        if not self.output_file.exists():
            self._processed_hashes_cache = processed_hashes
            return processed_hashes

        try:
            with open(self.output_file, encoding='utf-8') as f:
                for line in f:
                    try:
                        data = json.loads(line.strip())
                        url_hash = data.get('url_hash')
                        if url_hash:
                            processed_hashes.add(url_hash)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning(f"Failed to read processed hashes: {e}")

        self._processed_hashes_cache = processed_hashes
        logger.info(f"Loaded {len(processed_hashes)} already-processed URL hashes")
        return processed_hashes

    def _prioritize_batch_by_importance(self, batch: list[BatchQueueItem]) -> list[BatchQueueItem]:
        """
        Prioritize URLs in batch by link importance scores (PageRank, HITS).
        URLs with higher importance are validated first.
        """
        if not self.enable_link_graph or not self.link_graph:
            return batch

        # Get importance scores for each URL
        url_scores = []
        pagerank_values = []
        authority_values = []
        inlink_counts = []

        for item in batch:
            importance = self.link_graph.get_page_importance(item.url)
            pagerank_values.append(importance.pagerank_score)
            authority_values.append(importance.authority_score)
            inlink_counts.append(importance.inlink_count)

        # Normalize scores to [0, 1] range using min-max normalization
        def normalize(values):
            if not values:
                return values
            min_val = min(values)
            max_val = max(values)
            if max_val == min_val:
                # All values are the same, return uniform scores
                return [0.5] * len(values)
            return [(v - min_val) / (max_val - min_val) for v in values]

        norm_pagerank = normalize(pagerank_values)
        norm_authority = normalize(authority_values)
        norm_inlinks = normalize(inlink_counts)

        # Calculate combined scores with normalized values
        for i, item in enumerate(batch):
            combined_score = (
                norm_pagerank[i] * 0.4 +
                norm_authority[i] * 0.4 +
                norm_inlinks[i] * 0.2
            )
            url_scores.append((item, combined_score))

        # Sort by combined score (descending)
        url_scores.sort(key=lambda x: x[1], reverse=True)

        prioritized_batch = [item for item, score in url_scores]

        # Log prioritization statistics with min/max/avg
        scores_only = [score for _, score in url_scores]
        if scores_only:
            min_score = min(scores_only)
            max_score = max(scores_only)
            avg_score = sum(scores_only) / len(scores_only)
            top_10 = scores_only[:10]
            logger.debug(f"[Stage2] Batch priority stats - min: {min_score:.4f}, max: {max_score:.4f}, avg: {avg_score:.4f}, top 10: {[f'{s:.4f}' for s in top_10]}")

        return prioritized_batch

    async def validate_url(self, session: aiohttp.ClientSession | None, url: str, url_hash: str) -> ValidationResult:
        """Validate a single URL using HEAD with GET fallback."""

        if session is not None:
            return await self._validate_with_session(session, url, url_hash)

        # Create SSL context for single URL validation
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        connector = aiohttp.TCPConnector(
            limit=self.connector_limit,
            limit_per_host=10,
            ttl_dns_cache=300,
            ssl=ssl_context,
            enable_cleanup_closed=True
        )

        # Better headers for single URL validation
        headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=self.session_timeout,
            headers=headers
        ) as managed_session:
            return await self._validate_with_session(managed_session, url, url_hash)

    async def validate_batch(self, batch: list[BatchQueueItem], batch_id: int = 0):
        """Validate a batch of URLs concurrently with checkpoint support"""
        if not batch:
            return

        logger.info(f"Validating batch {batch_id} of {len(batch)} URLs")

        # Prioritize URLs by link importance scores
        batch = self._prioritize_batch_by_importance(batch)

        # Start checkpoint for this batch
        self.checkpoint.start_batch(
            stage="stage2_validation",
            batch_id=batch_id,
            metadata={"batch_size": len(batch), "output_file": str(self.output_file)}
        )

        # Create SSL context that's more permissive for development
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Create aiohttp session with connection pooling and SSL context
        connector = aiohttp.TCPConnector(
            limit=self.connector_limit,
            limit_per_host=10,
            ttl_dns_cache=300,
            ssl=ssl_context,
            enable_cleanup_closed=True
        )

        # Better headers to avoid blocking
        headers = {
            'User-Agent': self.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=self.session_timeout,
            headers=headers
        ) as session:

            # Create validation tasks
            tasks = [self.validate_url(session, item.url, item.url_hash) for item in batch]

            # Run validations concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Write results to output file with progress tracking
            successful_validations = 0
            with open(self.output_file, 'a', encoding='utf-8') as f:
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Validation task failed: {result}")
                        self.checkpoint.mark_failed(f"Task {i} failed: {result}")
                        continue

                    try:
                        f.write(json.dumps(asdict(result), ensure_ascii=False) + '\n')
                        successful_validations += 1

                        # Record feedback for Stage 2 -> Stage 1 learning
                        item = batch[i]
                        discovery_source = item.discovery_source if hasattr(item, 'discovery_source') else 'unknown'
                        discovery_depth = item.discovery_depth if hasattr(item, 'discovery_depth') else 0

                        # Determine if this is likely content (HTML)
                        has_content = result.is_valid and result.content_type and 'html' in result.content_type.lower()

                        if result.is_valid:
                            self.feedback_store.record_validation(
                                url=result.url,
                                discovery_source=discovery_source,
                                is_valid=True,
                                status_code=result.status_code
                            )

                            # Record for adaptive depth learning
                            self.adaptive_depth.record_validation(
                                url=result.url,
                                is_valid=True,
                                has_content=has_content,
                                word_count=0,  # We don't have word count in Stage 2
                                depth=discovery_depth
                            )
                        else:
                            # Record failure with error details
                            error_type = result.error_message.split(':')[0] if result.error_message else 'unknown'
                            self.feedback_store.record_validation(
                                url=result.url,
                                discovery_source=discovery_source,
                                is_valid=False,
                                status_code=result.status_code,
                                error_type=error_type
                            )

                            # Record for adaptive depth learning
                            self.adaptive_depth.record_validation(
                                url=result.url,
                                is_valid=False,
                                has_content=False,
                                word_count=0,
                                depth=discovery_depth
                            )

                        # Update checkpoint progress
                        self.checkpoint.update_progress(
                            processed_line=i + 1,
                            url_hash=result.url_hash,
                            total_processed=successful_validations
                        )
                    except Exception as e:
                        logger.error(f"Error writing validation result: {e}")

            # Mark batch as completed
            self.checkpoint.complete_batch(successful_validations)

        logger.debug(f"Completed validation of {len(batch)} URLs (batch {batch_id})")

    async def validate_from_file(self, input_file: Path) -> int:
        """Validate URLs from a Stage 1 discovery file"""
        if not input_file.exists():
            logger.error(f"Input file not found: {input_file}")
            return 0

        logger.info(f"Starting validation from {input_file}")

        if not self.output_file.exists() or self.output_file.stat().st_size == 0:
            logger.debug("Resetting checkpoint for new or empty output file")
            self.checkpoint.reset()

        batch_size = self.max_workers
        processed_count = 0
        batch: list[BatchQueueItem] = []
        batch_id = 0

        processed_hashes = self._get_processed_url_hashes()
        pending_hashes = set()

        # Check for resume point from previous checkpoint
        resume_point = self.checkpoint.get_resume_point()
        if resume_point['status'] == 'processing':
            logger.info(f"Resuming from batch {resume_point['batch_id']}, line {resume_point['last_processed_line']}")
            batch_id = resume_point['batch_id']

        with open(input_file, encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                # Skip lines we've already processed if resuming
                if self.checkpoint.should_skip_to_line(line_no):
                    continue

                try:
                    data = json.loads(line.strip())
                except json.JSONDecodeError as exc:
                    logger.error(f"Failed to parse JSON at line {line_no}: {exc}")
                    continue

                url = data.get('discovered_url', '')
                url_hash = data.get('url_hash', '')

                if url_hash:
                    if url_hash in processed_hashes or url_hash in pending_hashes:
                        continue
                    pending_hashes.add(url_hash)

                batch.append(
                    BatchQueueItem(
                        url=url,
                        url_hash=url_hash,
                        source_stage='stage1',
                        data=data,
                    )
                )

                if len(batch) == batch_size:
                    await self.validate_batch(batch, batch_id)
                    processed_count += len(batch)
                    if pending_hashes:
                        processed_hashes.update(pending_hashes)
                        pending_hashes.clear()
                    batch = []
                    batch_id += 1

                    if processed_count % 1000 == 0:
                        logger.info(f"Validated {processed_count} URLs")

        if batch:
            await self.validate_batch(batch, batch_id)
            processed_count += len(batch)
            if pending_hashes:
                processed_hashes.update(pending_hashes)
                pending_hashes.clear()

        logger.info(f"Validation completed: {processed_count} URLs processed")

        # Save feedback for Stage 1 to use in next crawl
        self.feedback_store.save_feedback()
        self.feedback_store.print_report()

        # Save adaptive depth configuration
        self.adaptive_depth.save_config()
        self.adaptive_depth.print_report()

        return processed_count

    async def _validate_with_session(self, session: aiohttp.ClientSession, url: str, url_hash: str) -> ValidationResult:
        """Execute validation using the provided session with retry logic."""
        start_time = time.perf_counter()

        
        # Retry configuration
        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries):
            try:
                # Skip HEAD request - aiohttp 3.12 has serialization bugs with HEAD
                # Just use GET directly
                return await self._perform_get(session, url, url_hash, start_time)

            except TimeoutError:
                if attempt == max_retries - 1:
                    return self._build_timeout_result(url, url_hash, start_time)
                await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff

            except aiohttp.ClientError as exc:
                if attempt == max_retries - 1:
                    return self._build_client_error_result(url, url_hash, exc, start_time)
                await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff

            except Exception as exc:
                # Unexpected error, don't retry
                import traceback
                logger.error(f"Validation error for {url}: {exc}")
                logger.error(traceback.format_exc())
                return self._build_client_error_result(url, url_hash, exc, start_time)

        # This should never be reached due to the logic above
        return self._build_timeout_result(url, url_hash, start_time)

    async def _perform_get(
        self,
        session: aiohttp.ClientSession,
        url: str,
        url_hash: str,
        start_time: float
    ) -> ValidationResult:
        """Perform GET request and build validation result."""

        async with session.get(url, allow_redirects=True) as response:
            body_bytes = await response.read()

            header_length = response.headers.get('Content-Length')
            content_type = response.headers.get('Content-Type', '') or ''
            status_code = response.status
            final_url = self._stringify_url(response, url)
            body_length = len(body_bytes)
            content_length = body_length
            if header_length is not None:
                try:
                    parsed_length = int(header_length)
                    if parsed_length >= 0 and parsed_length <= body_length:
                        content_length = parsed_length
                except (TypeError, ValueError):
                    pass

            response_time = (time.perf_counter() - start_time)

            # Capture freshness headers
            last_modified = response.headers.get('Last-Modified')
            etag = response.headers.get('ETag')
            cache_control = response.headers.get('Cache-Control')

            # Normalize content type
            normalized_content_type = content_type.split(';')[0].strip().lower()

            # Accept HTML, PDFs, and media files as valid
            valid_content_types = [
                'text/html',
                'application/pdf',
                'image/jpeg', 'image/png', 'image/gif', 'image/webp',
                'video/mp4', 'video/webm',
                'audio/mpeg', 'audio/wav'
            ]

            is_valid_content = any(normalized_content_type == ct for ct in valid_content_types)
            is_valid = 200 <= status_code < 400 and is_valid_content

            # Calculate staleness score
            staleness_score = self.freshness_tracker.update_freshness(
                url=final_url,
                url_hash=url_hash,
                last_modified=last_modified,
                etag=etag,
                content_type=normalized_content_type,
                content_changed=False  # We don't have previous content to compare
            )

            return ValidationResult(
                url=final_url,
                url_hash=url_hash,
                status_code=status_code,
                content_type=content_type,
                content_length=content_length,
                response_time=response_time,
                is_valid=is_valid,
                error_message=None if is_valid else f'Invalid response or unsupported content type: {normalized_content_type}',
                validated_at=datetime.now().isoformat(),
                last_modified=last_modified,
                etag=etag,
                staleness_score=staleness_score,
                cache_control=cache_control
            )

    def _evaluate_head_response(
        self,
        response: aiohttp.ClientResponse,
        url: str,
        url_hash: str,
        start_time: float
    ) -> ValidationResult | None:
        """Return a validation result if HEAD response is sufficient, else None."""

        content_type = response.headers.get('Content-Type', '') or ''
        status_code = response.status

        # Normalize content type
        normalized_content_type = content_type.split(';')[0].strip().lower()

        # Accept HTML, PDFs, and media files
        valid_content_types = [
            'text/html',
            'application/pdf',
            'image/jpeg', 'image/png', 'image/gif', 'image/webp',
            'video/mp4', 'video/webm',
            'audio/mpeg', 'audio/wav'
        ]

        is_valid_content = any(normalized_content_type == ct for ct in valid_content_types)

        if not (200 <= status_code < 400 and is_valid_content):
            return None

        content_length = self._parse_content_length(response.headers.get('Content-Length'))
        response_time = (time.perf_counter() - start_time)
        final_url = self._stringify_url(response, url)

        return ValidationResult(
            url=final_url,
            url_hash=url_hash,
            status_code=status_code,
            content_type=content_type,
            content_length=content_length,
            response_time=response_time,
            is_valid=True,
            error_message=None,
            validated_at=datetime.now().isoformat()
        )

    def _build_timeout_result(self, url: str, url_hash: str, start_time: float) -> ValidationResult:
        response_time = (time.perf_counter() - start_time)
        return ValidationResult(
            url=url,
            url_hash=url_hash,
            status_code=0,
            content_type='',
            content_length=0,
            response_time=response_time,
            is_valid=False,
            error_message='Request timeout',
            validated_at=datetime.now().isoformat()
        )

    def _build_client_error_result(
        self,
        url: str,
        url_hash: str,
        exc: Exception,
        start_time: float
    ) -> ValidationResult:
        response_time = (time.perf_counter() - start_time)
        if isinstance(exc, aiohttp.ClientError):
            message = f"{type(exc).__name__}: {exc}"
        else:
            message = str(exc) or exc.__class__.__name__
        return ValidationResult(
            url=url,
            url_hash=url_hash,
            status_code=0,
            content_type='',
            content_length=0,
            response_time=response_time,
            is_valid=False,
            error_message=message,
            validated_at=datetime.now().isoformat()
        )

    @staticmethod
    def _stringify_url(response: aiohttp.ClientResponse, fallback: str) -> str:
        raw_url = getattr(response, 'url', None)
        if raw_url is None:
            return fallback
        try:
            return str(raw_url)
        except Exception:
            return fallback

    @staticmethod
    def _parse_content_length(value: str | None) -> int:
        if value is None:
            return 0
        try:
            parsed = int(value)
            return max(parsed, 0)
        except (TypeError, ValueError):
            return 0
