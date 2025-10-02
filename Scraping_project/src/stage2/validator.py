# TODO: Add support for more flexible validation logic, such as allowing the user to specify custom validation rules.
import asyncio
import inspect
import aiohttp
import json
import logging
import ssl
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import asdict

from src.orchestrator.pipeline import BatchQueueItem
from src.common.schemas import ValidationResult
from src.common.checkpoints import CheckpointManager
from src.common.feedback import FeedbackStore
from src.common.adaptive_depth import AdaptiveDepthManager
from src.common import config_keys as keys

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
                def __init__(self, message: str = "", os_error: Optional[BaseException] = None):
                    super().__init__(message)
                    self.os_error = os_error

            aiohttp.ClientSSLError = _CompatClientSSLError


class URLValidator:
    """Stage 2 Validator - async client for URL validation using HEAD/GET checks"""

    # TODO: The max_workers is hardcoded. It should be configurable.
    def __init__(self, config):
        self.config = config
        self.stage2_config = config.get_stage2_config()
        self.max_workers = self.stage2_config['max_workers']
        self.timeout = self.stage2_config['timeout']
        self.output_file = Path(self.stage2_config['output_file'])

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

        # Ensure output directory exists
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        # Session configuration
        self.connector_limit = min(self.max_workers * 2, 100)
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
            with open(self.output_file, 'r', encoding='utf-8') as f:
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

    async def validate_url(self, session: Optional[aiohttp.ClientSession], url: str, url_hash: str) -> ValidationResult:
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
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
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

    async def validate_batch(self, batch: List[BatchQueueItem], batch_id: int = 0):
        """Validate a batch of URLs concurrently with checkpoint support"""
        if not batch:
            return

        logger.info(f"Validating batch {batch_id} of {len(batch)} URLs")

        # Filter out already-processed URLs for idempotency
        processed_hashes = self._get_processed_url_hashes()
        batch = [item for item in batch if item.url_hash not in processed_hashes]

        if not batch:
            logger.info(f"Batch {batch_id}: All URLs already processed, skipping")
            return

        logger.info(f"Batch {batch_id}: {len(batch)} new URLs to validate after deduplication")

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
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
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

        batch_size = self.max_workers
        processed_count = 0
        batch: List[BatchQueueItem] = []
        batch_id = 0

        # Check for resume point from previous checkpoint
        resume_point = self.checkpoint.get_resume_point()
        if resume_point['status'] == 'processing':
            logger.info(f"Resuming from batch {resume_point['batch_id']}, line {resume_point['last_processed_line']}")
            batch_id = resume_point['batch_id']

        with open(input_file, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                # Skip lines we've already processed if resuming
                if self.checkpoint.should_skip_to_line(line_no):
                    continue

                try:
                    data = json.loads(line.strip())
                except json.JSONDecodeError as exc:
                    logger.error(f"Failed to parse JSON at line {line_no}: {exc}")
                    continue

                batch.append(
                    BatchQueueItem(
                        url=data.get('discovered_url', ''),
                        url_hash=data.get('url_hash', ''),
                        source_stage='stage1',
                        data=data,
                    )
                )

                if len(batch) == batch_size:
                    await self.validate_batch(batch, batch_id)
                    processed_count += len(batch)
                    batch = []
                    batch_id += 1

                    if processed_count % 1000 == 0:
                        logger.info(f"Validated {processed_count} URLs")

        if batch:
            await self.validate_batch(batch, batch_id)
            processed_count += len(batch)

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
        start_time = datetime.now()

        # TODO: The retry logic is very basic. It should be made more flexible, such as allowing the user to specify different retry strategies for different error types.
        # Retry configuration
        max_retries = 3
        retry_delay = 1.0

        for attempt in range(max_retries):
            try:
                if hasattr(session, 'head'):
                    try:
                        async with session.head(url, allow_redirects=True) as head_response:
                            head_result = self._evaluate_head_response(head_response, url, url_hash, start_time)
                            if head_result is not None:
                                return head_result
                    except (asyncio.TimeoutError, aiohttp.ClientError):
                        # Fall through to GET request
                        pass

                return await self._perform_get(session, url, url_hash, start_time)

            except asyncio.TimeoutError:
                if attempt == max_retries - 1:
                    return self._build_timeout_result(url, url_hash, start_time)
                await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff

            except aiohttp.ClientError as exc:
                if attempt == max_retries - 1:
                    return self._build_client_error_result(url, url_hash, exc, start_time)
                await asyncio.sleep(retry_delay * (2 ** attempt))  # Exponential backoff

            except Exception as exc:
                # Unexpected error, don't retry
                return self._build_client_error_result(url, url_hash, exc, start_time)

        # This should never be reached due to the logic above
        return self._build_timeout_result(url, url_hash, start_time)

    async def _perform_get(
        self,
        session: aiohttp.ClientSession,
        url: str,
        url_hash: str,
        start_time: datetime
    ) -> ValidationResult:
        """Perform GET request and build validation result."""

        async with session.get(url, allow_redirects=True) as response:
            body_bytes = await response.read()

            content_type = response.headers.get('Content-Type', '') or ''
            status_code = response.status
            final_url = self._stringify_url(response, url)
            content_length = len(body_bytes)
            response_time = (datetime.now() - start_time).total_seconds()

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

            return ValidationResult(
                url=final_url,
                url_hash=url_hash,
                status_code=status_code,
                content_type=content_type,
                content_length=content_length,
                response_time=response_time,
                is_valid=is_valid,
                error_message=None if is_valid else f'Invalid response or unsupported content type: {normalized_content_type}',
                validated_at=datetime.now().isoformat()
            )

    def _evaluate_head_response(
        self,
        response: aiohttp.ClientResponse,
        url: str,
        url_hash: str,
        start_time: datetime
    ) -> Optional[ValidationResult]:
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
        response_time = (datetime.now() - start_time).total_seconds()
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

    def _build_timeout_result(self, url: str, url_hash: str, start_time: datetime) -> ValidationResult:
        response_time = (datetime.now() - start_time).total_seconds()
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
        start_time: datetime
    ) -> ValidationResult:
        response_time = (datetime.now() - start_time).total_seconds()
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
    def _parse_content_length(value: Optional[str]) -> int:
        if value is None:
            return 0
        try:
            parsed = int(value)
            return max(parsed, 0)
        except (TypeError, ValueError):
            return 0
