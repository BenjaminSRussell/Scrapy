import asyncio
import aiohttp
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import asdict

from orchestrator.pipeline import BatchQueueItem
from common.schemas import ValidationResult

logger = logging.getLogger(__name__)


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

    async def validate_url(self, session: aiohttp.ClientSession, url: str, url_hash: str) -> ValidationResult:
        """Validate a single URL using a straightforward GET request."""

        start_time = datetime.now()

        try:
            async with session.get(url, allow_redirects=True) as response:
                body_bytes = await response.read()

                content_type = response.headers.get('Content-Type', '')
                status_code = response.status

                try:
                    header_length = response.headers.get('Content-Length')
                    content_length = int(header_length) if header_length is not None else len(body_bytes)
                except (TypeError, ValueError):
                    content_length = len(body_bytes)

                response_time = (datetime.now() - start_time).total_seconds()

                is_html = 'text/html' in content_type.lower()
                is_valid = 200 <= status_code < 400 and is_html

                return ValidationResult(
                    url=str(response.url),
                    url_hash=url_hash,
                    status_code=status_code,
                    content_type=content_type,
                    content_length=content_length,
                    response_time=response_time,
                    is_valid=is_valid,
                    error_message=None if is_valid else 'Invalid response',
                    validated_at=datetime.now().isoformat()
                )

        except asyncio.TimeoutError:
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

        except aiohttp.ClientError as exc:
            response_time = (datetime.now() - start_time).total_seconds()
            return ValidationResult(
                url=url,
                url_hash=url_hash,
                status_code=0,
                content_type='',
                content_length=0,
                response_time=response_time,
                is_valid=False,
                error_message=str(exc),
                validated_at=datetime.now().isoformat()
            )

    async def validate_batch(self, batch: List[BatchQueueItem]):
        """Validate a batch of URLs concurrently"""
        if not batch:
            return

        logger.info(f"Validating batch of {len(batch)} URLs")

        # Create aiohttp session with connection pooling
        connector = aiohttp.TCPConnector(
            limit=self.connector_limit,
            limit_per_host=10,
            ttl_dns_cache=300
        )

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=self.session_timeout,
            headers={'User-Agent': self.config.get('scrapy', 'user_agent')}
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
