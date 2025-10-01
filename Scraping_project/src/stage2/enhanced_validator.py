"""
Enhanced Stage 2 Validator with intelligent retry, circuit breakers, and content classification.
Goes beyond "is it HTML?" to provide rich metadata for Stage 3.
"""

import asyncio
import aiohttp
import json
import logging
import ssl
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import asdict, replace
from urllib.parse import urlparse

from src.orchestrator.pipeline import BatchQueueItem
from src.common.schemas import ValidationResult
from src.common.checkpoints import CheckpointManager
from src.common import config_keys as keys
from src.common.retry_strategies import (
    RetryConfig,
    DomainCircuitBreaker,
    classify_error,
    calculate_backoff_delay,
    should_retry,
    ErrorType
)
from src.common.content_classification import ContentClassifier, classify_content

logger = logging.getLogger(__name__)


class EnhancedURLValidator:
    """
    Enhanced Stage 2 Validator with:
    - Intelligent retry with jittered exponential backoff
    - Domain-specific circuit breakers
    - Content classification beyond HTML
    - Rich metadata for Stage 3
    """

    def __init__(self, config):
        """
        Initialize enhanced validator

        Args:
            config: Pipeline configuration object
        """
        self.config = config
        self.stage2_config = config.get_stage2_config()
        self.max_workers = self.stage2_config['max_workers']
        self.timeout = self.stage2_config['timeout']
        self.output_file = Path(self.stage2_config['output_file'])

        # Get retry configuration from config or use defaults
        retry_config_dict = self.stage2_config.get('retry', {})
        self.retry_config = RetryConfig(
            max_attempts=retry_config_dict.get('max_attempts', 3),
            base_delay=retry_config_dict.get('base_delay', 1.0),
            max_delay=retry_config_dict.get('max_delay', 60.0),
            exponential_base=retry_config_dict.get('exponential_base', 2.0),
            jitter_factor=retry_config_dict.get('jitter_factor', 0.1),
            transient_max_attempts=retry_config_dict.get('transient_max_attempts', 5),
            rate_limit_max_attempts=retry_config_dict.get('rate_limit_max_attempts', 3),
            rate_limit_base_delay=retry_config_dict.get('rate_limit_base_delay', 5.0)
        )

        # Get circuit breaker configuration from config or use defaults
        cb_config_dict = self.stage2_config.get('circuit_breaker', {})
        self.circuit_breaker = DomainCircuitBreaker(
            failure_threshold=cb_config_dict.get('failure_threshold', 5),
            success_threshold=cb_config_dict.get('success_threshold', 2),
            timeout=cb_config_dict.get('timeout', 60.0)
        )

        # Initialize content classifier
        self.content_classifier = ContentClassifier()

        # Initialize checkpoint manager
        checkpoint_dir = Path("data/checkpoints")
        self.checkpoint_manager = CheckpointManager(checkpoint_dir)
        self.checkpoint = self.checkpoint_manager.get_checkpoint("stage2_enhanced_validation")

        # Ensure output directory exists
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        # Session configuration
        self.connector_limit = min(self.max_workers * 2, 100)
        self.session_timeout = aiohttp.ClientTimeout(total=self.timeout)

        # Cache of processed URL hashes
        self._processed_hashes_cache = None

        # Statistics
        self.stats = {
            'total_validated': 0,
            'successful': 0,
            'failed': 0,
            'retries': 0,
            'circuit_breaker_blocks': 0,
            'by_error_type': {},
            'by_content_category': {}
        }

        logger.info(
            f"Enhanced validator initialized - "
            f"Retry: max_attempts={self.retry_config.max_attempts}, "
            f"base_delay={self.retry_config.base_delay}s, "
            f"jitter={self.retry_config.jitter_factor} | "
            f"Circuit breaker: failure_threshold={self.circuit_breaker.failure_threshold}, "
            f"timeout={self.circuit_breaker.timeout}s"
        )

    def _get_processed_url_hashes(self) -> set:
        """Get set of already-processed URL hashes for idempotency"""
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

    async def validate_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
        url_hash: str
    ) -> ValidationResult:
        """
        Validate URL with intelligent retry and classification

        Args:
            session: aiohttp session
            url: URL to validate
            url_hash: Hash of URL

        Returns:
            ValidationResult with enhanced metadata
        """
        domain = urlparse(url).netloc
        start_time = datetime.now()

        # Check circuit breaker
        is_allowed, reason = await self.circuit_breaker.is_allowed(domain)
        if not is_allowed:
            self.stats['circuit_breaker_blocks'] += 1
            logger.info(f"Circuit breaker blocked {domain}: {reason}")

            return ValidationResult(
                url=url,
                url_hash=url_hash,
                status_code=0,
                content_type='',
                content_length=0,
                response_time=0.0,
                is_valid=False,
                error_message=f"Circuit breaker open: {reason}",
                validated_at=datetime.now().isoformat()
            )

        # Retry with backoff
        for attempt in range(self.retry_config.max_attempts + 1):
            try:
                result = await self._attempt_validation(session, url, url_hash, start_time)

                # Record success with circuit breaker
                await self.circuit_breaker.record_success(domain)

                self.stats['successful'] += 1
                if attempt > 0:
                    logger.info(f"✅ {url} succeeded on attempt {attempt + 1}")

                return result

            except Exception as e:
                # Classify error
                error_type = classify_error(exception=e)

                # Track error type
                error_type_str = error_type.value
                if error_type_str not in self.stats['by_error_type']:
                    self.stats['by_error_type'][error_type_str] = 0
                self.stats['by_error_type'][error_type_str] += 1

                # Record failure with circuit breaker
                await self.circuit_breaker.record_failure(domain, error_type)

                # Determine if should retry
                should_continue, retry_reason = should_retry(attempt, error_type, self.retry_config)

                if not should_continue:
                    self.stats['failed'] += 1
                    logger.warning(f"❌ {url}: {retry_reason}")

                    return self._build_error_result(
                        url, url_hash, e, error_type, start_time
                    )

                # Calculate backoff delay
                delay = calculate_backoff_delay(attempt, self.retry_config, error_type)

                self.stats['retries'] += 1
                logger.info(
                    f"🔄 {url} attempt {attempt + 1} failed ({error_type.value}). "
                    f"Retrying in {delay:.2f}s..."
                )

                await asyncio.sleep(delay)

        # Should never reach here, but handle just in case
        self.stats['failed'] += 1
        return self._build_error_result(
            url, url_hash, Exception("Max retries exceeded"), ErrorType.UNKNOWN, start_time
        )

    async def _attempt_validation(
        self,
        session: aiohttp.ClientSession,
        url: str,
        url_hash: str,
        start_time: datetime
    ) -> ValidationResult:
        """
        Attempt validation (single try)

        Returns:
            ValidationResult with classification metadata
        """
        # Try HEAD first for efficiency
        try:
            async with session.head(url, allow_redirects=True) as response:
                # If HEAD works, check if we have enough info
                content_type = response.headers.get('Content-Type', '')
                status_code = response.status

                if status_code < 400:
                    # HEAD success, get content-length if available
                    content_length = self._parse_content_length(
                        response.headers.get('Content-Length')
                    )

                    # If we have reasonable info, classify and return
                    if content_type or content_length > 0:
                        return await self._build_result_with_classification(
                            response, url, url_hash, start_time, None, 'HEAD'
                        )
        except (asyncio.TimeoutError, aiohttp.ClientError):
            # HEAD failed or timeout, fall through to GET
            pass

        # GET request
        async with session.get(url, allow_redirects=True) as response:
            body_bytes = await response.read()

            return await self._build_result_with_classification(
                response, url, url_hash, start_time, body_bytes, 'GET'
            )

    async def _build_result_with_classification(
        self,
        response: aiohttp.ClientResponse,
        url: str,
        url_hash: str,
        start_time: datetime,
        body_bytes: Optional[bytes],
        method: str
    ) -> ValidationResult:
        """
        Build ValidationResult with content classification

        Args:
            response: aiohttp response
            url: Original URL
            url_hash: URL hash
            start_time: Request start time
            body_bytes: Response body (if available)
            method: HTTP method used (HEAD/GET)

        Returns:
            ValidationResult with enhanced metadata
        """
        content_type = response.headers.get('Content-Type', '') or ''
        status_code = response.status
        final_url = str(response.url)
        response_time = (datetime.now() - start_time).total_seconds()

        # Determine content length
        if body_bytes is not None:
            content_length = len(body_bytes)
        else:
            content_length = self._parse_content_length(
                response.headers.get('Content-Length')
            )

        # Classify content
        headers_dict = {k: v for k, v in response.headers.items()}
        classification = self.content_classifier.classify(
            status_code=status_code,
            content_type=content_type,
            content_length=content_length,
            url=final_url,
            headers=headers_dict
        )

        # Track content category
        category = classification.category.value
        if category not in self.stats['by_content_category']:
            self.stats['by_content_category'][category] = 0
        self.stats['by_content_category'][category] += 1

        # Determine if valid (more nuanced than just HTML)
        is_valid = classification.is_enrichable and status_code < 400

        # Build validation result with enhanced metadata
        return ValidationResult(
            url=final_url,
            url_hash=url_hash,
            status_code=status_code,
            content_type=content_type,
            content_length=content_length,
            response_time=response_time,
            is_valid=is_valid,
            error_message=None if is_valid else f"Not enrichable: {classification.category.value}",
            validated_at=datetime.now().isoformat(),
            validation_method=method,
            # Enhanced metadata from classification
            server_headers={
                'content_type': content_type,
                'content_length': str(content_length),
                'content_category': classification.category.value,
                'content_quality': classification.quality.value,
                'is_enrichable': str(classification.is_enrichable),
                'confidence': str(classification.confidence),
                'recommendations': ','.join(classification.recommendations)
            },
            network_metadata={
                'response_time_ms': str(int(response_time * 1000)),
                'final_url_changed': str(url != final_url),
                'classification_metadata': str(classification.metadata)
            }
        )

    def _build_error_result(
        self,
        url: str,
        url_hash: str,
        exception: Exception,
        error_type: ErrorType,
        start_time: datetime
    ) -> ValidationResult:
        """Build ValidationResult for error cases"""
        response_time = (datetime.now() - start_time).total_seconds()

        return ValidationResult(
            url=url,
            url_hash=url_hash,
            status_code=0,
            content_type='',
            content_length=0,
            response_time=response_time,
            is_valid=False,
            error_message=f"{error_type.value}: {str(exception)}",
            validated_at=datetime.now().isoformat(),
            server_headers={'error_type': error_type.value},
            network_metadata={'exception_class': exception.__class__.__name__}
        )

    async def validate_batch(self, batch: List[BatchQueueItem], batch_id: int = 0):
        """Validate batch of URLs with enhanced features"""
        if not batch:
            return

        logger.info(f"Validating batch {batch_id} of {len(batch)} URLs")

        # Filter out already-processed URLs
        processed_hashes = self._get_processed_url_hashes()
        batch = [item for item in batch if item.url_hash not in processed_hashes]

        if not batch:
            logger.info(f"Batch {batch_id}: All URLs already processed")
            return

        logger.info(f"Batch {batch_id}: {len(batch)} new URLs to validate")

        # Start checkpoint
        self.checkpoint.start_batch(
            stage="stage2_enhanced_validation",
            batch_id=batch_id,
            metadata={"batch_size": len(batch)}
        )

        # Create SSL context
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Create aiohttp session
        connector = aiohttp.TCPConnector(
            limit=self.connector_limit,
            limit_per_host=10,
            ttl_dns_cache=300,
            ssl=ssl_context,
            enable_cleanup_closed=True
        )

        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive'
        }

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=self.session_timeout,
            headers=headers
        ) as session:
            # Create validation tasks
            tasks = [
                self.validate_url(session, item.url, item.url_hash)
                for item in batch
            ]

            # Run validations concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Write results
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
                        self.stats['total_validated'] += 1

                        self.checkpoint.update_progress(
                            processed_line=i + 1,
                            url_hash=result.url_hash,
                            total_processed=successful_validations
                        )
                    except Exception as e:
                        logger.error(f"Error writing result: {e}")

            self.checkpoint.complete_batch(successful_validations)

        logger.info(f"Batch {batch_id} complete: {successful_validations}/{len(batch)} validated")

    async def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics"""
        circuit_breaker_stats = await self.circuit_breaker.get_stats()

        return {
            'validation_stats': self.stats,
            'circuit_breaker_stats': circuit_breaker_stats
        }

    @staticmethod
    def _parse_content_length(value: Optional[str]) -> int:
        """Parse content length header"""
        if value is None:
            return 0
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0
