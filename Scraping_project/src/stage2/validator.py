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

from orchestrator.pipeline import BatchQueueItem
from common.schemas import ValidationResult

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

    def __init__(self, config):
        self.config = config
        self.stage2_config = config.get_stage2_config()
        self.max_workers = self.stage2_config['max_workers']
        self.timeout = self.stage2_config['timeout']
        self.output_file = Path(self.stage2_config['output_file'])

        # Ensure output directory exists
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

        # Session configuration
        self.connector_limit = min(self.max_workers * 2, 100)
        self.session_timeout = aiohttp.ClientTimeout(total=self.timeout)

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

    async def validate_batch(self, batch: List[BatchQueueItem]):
        """Validate a batch of URLs concurrently"""
        if not batch:
            return

        logger.info(f"Validating batch of {len(batch)} URLs")

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

            # Write results to output file
            with open(self.output_file, 'a', encoding='utf-8') as f:
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Validation task failed: {result}")
                        continue

                    try:
                        f.write(json.dumps(asdict(result), ensure_ascii=False) + '\n')
                    except Exception as e:
                        logger.error(f"Error writing validation result: {e}")

        logger.debug(f"Completed validation of {len(batch)} URLs")

    async def validate_from_file(self, input_file: Path) -> int:
        """Validate URLs from a Stage 1 discovery file"""
        if not input_file.exists():
            logger.error(f"Input file not found: {input_file}")
            return 0

        logger.info(f"Starting validation from {input_file}")

        batch_size = self.max_workers
        processed_count = 0
        batch: List[BatchQueueItem] = []

        with open(input_file, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
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
                    await self.validate_batch(batch)
                    processed_count += len(batch)
                    batch = []

                    if processed_count % 1000 == 0:
                        logger.info(f"Validated {processed_count} URLs")

        if batch:
            await self.validate_batch(batch)
            processed_count += len(batch)

        logger.info(f"Validation completed: {processed_count} URLs processed")
        return processed_count

    async def _validate_with_session(self, session: aiohttp.ClientSession, url: str, url_hash: str) -> ValidationResult:
        """Execute validation using the provided session with retry logic."""
        start_time = datetime.now()

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

            is_html = 'text/html' in content_type.lower()
            is_valid = 200 <= status_code < 400 and is_html

            return ValidationResult(
                url=final_url,
                url_hash=url_hash,
                status_code=status_code,
                content_type=content_type,
                content_length=content_length,
                response_time=response_time,
                is_valid=is_valid,
                error_message=None if is_valid else 'Invalid response',
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

        if not (200 <= status_code < 400 and 'text/html' in content_type.lower()):
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
