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
        """Validate a single URL using HEAD request, fallback to GET if needed"""
        start_time = datetime.now()

        try:
            # Try HEAD request first (faster)
            async with session.head(url, allow_redirects=True) as response:
                content_type = response.headers.get('Content-Type', '')

                # CRITICAL FIX: Safe Content-Length parsing
                # int(response.headers.get('Content-Length', 0)) raises ValueError
                # when header is blank/non-numeric, causing spurious failures
                try:
                    content_length = int(response.headers.get('Content-Length', '0'))
                except (ValueError, TypeError):
                    content_length = 0

                # If HEAD doesn't provide enough info, try GET with partial content
                if content_length == 0 or not content_type:
                    async with session.get(url, allow_redirects=True) as get_response:
                        content_type = get_response.headers.get('Content-Type', content_type)

                        # CRITICAL FIX: Safe Content-Length parsing on GET fallback
                        try:
                            content_length_header = get_response.headers.get('Content-Length', '')
                            content_length = int(content_length_header) if content_length_header else len(await get_response.read())
                        except (ValueError, TypeError):
                            # Fallback to actual content length if header is malformed
                            content_length = len(await get_response.read())

                        status_code = get_response.status
                else:
                    status_code = response.status

                response_time = (datetime.now() - start_time).total_seconds()

                return ValidationResult(
                    url=str(response.url),  # Final URL after redirects
                    url_hash=url_hash,
                    status_code=status_code,
                    content_type=content_type,
                    content_length=content_length,
                    response_time=response_time,
                    is_valid=200 <= status_code < 400 and 'text/html' in content_type.lower(),
                    error_message=None,
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

        except Exception as e:
            response_time = (datetime.now() - start_time).total_seconds()
            return ValidationResult(
                url=url,
                url_hash=url_hash,
                status_code=0,
                content_type='',
                content_length=0,
                response_time=response_time,
                is_valid=False,
                error_message=str(e),
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
            tasks = [
                self.validate_url(session, item.url, item.url_hash)
                for item in batch
            ]

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

        # Load URLs from file
        urls_to_validate = []
        with open(input_file, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                try:
                    data = json.loads(line.strip())
                    item = BatchQueueItem(
                        url=data.get('discovered_url', ''),
                        url_hash=data.get('url_hash', ''),
                        source_stage='stage1',
                        data=data
                    )
                    urls_to_validate.append(item)

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse JSON at line {line_no}: {e}")

        logger.info(f"Loaded {len(urls_to_validate)} URLs for validation")

        # Process in batches
        batch_size = self.max_workers
        processed_count = 0

        for i in range(0, len(urls_to_validate), batch_size):
            batch = urls_to_validate[i:i + batch_size]
            await self.validate_batch(batch)
            processed_count += len(batch)

            if processed_count % 1000 == 0:
                logger.info(f"Validated {processed_count}/{len(urls_to_validate)} URLs")

        logger.info(f"Validation completed: {processed_count} URLs processed")
        return processed_count