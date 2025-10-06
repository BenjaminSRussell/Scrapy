"""
Stage 2 Asynchronous Worker
High-concurrency worker for page analysis & quality control.
Downloads and analyzes URLs from stage1_discovery table.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List

import aiohttp
from bs4 import BeautifulSoup

from src.common.delta_lake import get_delta_manager

logger = logging.getLogger(__name__)


class Stage2Worker:
    """Async worker for Stage 2 page analysis with quality control."""

    def __init__(self, max_concurrent: int = 50, batch_size: int = 100):
        """
        Initialize Stage 2 worker.

        Args:
            max_concurrent: Max concurrent HTTP requests
            batch_size: Number of URLs to process per batch
        """
        self.max_concurrent = max_concurrent
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.delta = get_delta_manager()

        # Quality thresholds
        self.MIN_WORD_COUNT = 50
        self.MIN_TEXT_TO_HTML_RATIO = 0.1
        self.MASSIVE_DOC_THRESHOLD = 50000  # 50k characters

    async def run(self):
        """Main worker loop - process all pending URLs."""
        logger.info(f"Stage 2 Worker starting with {self.max_concurrent} concurrent workers")

        # Read all URLs from stage1_discovery
        all_urls = self.delta.read('stage1_discovery')

        if not all_urls:
            logger.warning("No URLs found in stage1_discovery")
            return

        # Read already processed URLs from stage2_page_analysis
        try:
            processed = self.delta.read('stage2_page_analysis')
            processed_hashes = {r['url_hash'] for r in processed}
        except Exception:
            processed_hashes = set()

        # Filter to pending URLs only
        pending = [
            url for url in all_urls
            if url.get('url_hash') not in processed_hashes
        ]

        logger.info(f"Found {len(pending)} pending URLs to analyze (out of {len(all_urls)} total)")

        if not pending:
            logger.info("No pending URLs to process")
            return

        # Process in batches
        for i in range(0, len(pending), self.batch_size):
            batch = pending[i:i + self.batch_size]
            logger.info(f"Processing batch {i // self.batch_size + 1}: {len(batch)} URLs")

            # Process batch concurrently
            tasks = [self._analyze_url(record) for record in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter successful results
            valid_results = [r for r in results if isinstance(r, dict) and not isinstance(r, Exception)]

            # Save to Delta Lake
            if valid_results:
                self.delta.write('stage2_page_analysis', valid_results, mode='append', async_write=False)
                logger.info(f"Saved {len(valid_results)} analysis results")

        logger.info("Stage 2 Worker completed all batches")

    async def _analyze_url(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze single URL with quality control and triage."""
        url = record.get('url')
        url_hash = record.get('url_hash')
        is_heavy = record.get('is_heavy', False)

        async with self.semaphore:
            try:
                timeout = aiohttp.ClientTimeout(total=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, allow_redirects=True) as response:
                        if response.status >= 400:
                            return self._error_record(url, url_hash, response.status, 'http_error')

                        content_type = response.headers.get('Content-Type', '').lower()

                        # Route based on content type
                        if 'text/html' in content_type:
                            html = await response.text()
                            return await self._analyze_html(url, url_hash, html, is_heavy)
                        elif 'application/pdf' in content_type:
                            # PDF handling - route to stage4 for large docs
                            return self._route_pdf_to_stage4(url, url_hash)
                        else:
                            # Other content types - minimal processing
                            return self._minimal_record(url, url_hash, content_type)

            except asyncio.TimeoutError:
                return self._error_record(url, url_hash, 0, 'timeout')
            except aiohttp.ClientError as e:
                return self._error_record(url, url_hash, 0, f'client_error: {type(e).__name__}')
            except Exception as e:
                logger.error(f"Failed to analyze {url}: {e}")
                return self._error_record(url, url_hash, 0, f'error: {str(e)}')

    async def _analyze_html(self, url: str, url_hash: str, html: str, is_heavy: bool) -> Dict[str, Any]:
        """Analyze HTML content with quality control."""
        soup = BeautifulSoup(html, 'html.parser')

        # Extract title
        title_tag = soup.find('title')
        title = title_tag.get_text(strip=True) if title_tag else 'Untitled'

        # Remove noise
        for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'iframe']):
            tag.decompose()

        # Extract text
        text = soup.get_text(separator=' ', strip=True)
        text = ' '.join(text.split())  # Clean whitespace

        # Quality metrics
        word_count = len(text.split())
        content_length = len(text)
        html_length = len(html)
        text_to_html_ratio = content_length / html_length if html_length > 0 else 0

        # Quality checks
        is_low_quality = (
            word_count < self.MIN_WORD_COUNT or
            text_to_html_ratio < self.MIN_TEXT_TO_HTML_RATIO
        )

        # Document triage - massive docs go to Stage 4
        is_massive_doc = content_length > self.MASSIVE_DOC_THRESHOLD

        if is_massive_doc and not is_low_quality:
            await self._route_to_stage4(url, url_hash, text, word_count, content_length)
            logger.info(f"Routed large doc ({content_length} chars) to Stage 4: {url[:80]}")

        # Extract keywords using YAKE (only for non-massive, quality docs)
        keywords = []
        if not is_low_quality and not is_massive_doc:
            keywords = await self._extract_keywords_async(text, is_heavy)

        # Quality score
        quality_score = self._calculate_quality_score(word_count, text_to_html_ratio)

        return {
            'url': url,
            'url_hash': url_hash,
            'title': title,
            'word_count': word_count,
            'content_length': content_length,
            'html_length': html_length,
            'text_to_html_ratio': round(text_to_html_ratio, 3),
            'is_low_quality': is_low_quality,
            'is_massive_doc': is_massive_doc,
            'quality_score': quality_score,
            'text_content': text[:10000] if not is_low_quality else '',  # First 10k chars
            'keywords': keywords,
            'has_error': False,
            'processed_at': datetime.now().isoformat(),
        }

    async def _extract_keywords_async(self, text: str, is_heavy: bool) -> List[str]:
        """Extract keywords using YAKE in async context."""
        if not text or len(text) < 50:
            return []

        try:
            # Run YAKE in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            keywords = await loop.run_in_executor(None, self._extract_keywords_sync, text, is_heavy)
            return keywords
        except Exception as e:
            logger.warning(f"YAKE extraction failed: {e}")
            return []

    def _extract_keywords_sync(self, text: str, is_heavy: bool) -> List[str]:
        """Synchronous YAKE keyword extraction."""
        try:
            import yake

            max_keywords = 20 if is_heavy else 10

            kw_extractor = yake.KeywordExtractor(
                lan="en",
                n=3,
                dedupLim=0.9,
                top=max_keywords,
            )

            keywords = kw_extractor.extract_keywords(text[:5000])
            return [kw[0] for kw in keywords]

        except ImportError:
            logger.warning("YAKE not installed")
            return []
        except Exception as e:
            logger.warning(f"YAKE failed: {e}")
            return []

    def _calculate_quality_score(self, word_count: int, text_ratio: float) -> float:
        """Calculate quality score 0-1."""
        word_score = min(word_count / 1000, 0.6)
        ratio_score = min(text_ratio * 0.4, 0.4)
        return round(word_score + ratio_score, 3)

    async def _route_to_stage4(self, url: str, url_hash: str, text: str, word_count: int, content_length: int):
        """Route large document to Stage 4 for heavyweight processing."""
        record = {
            'url': url,
            'url_hash': url_hash,
            'text': text,
            'word_count': word_count,
            'content_length': content_length,
            'status': 'pending',
            'queued_at': datetime.now().isoformat(),
        }

        try:
            self.delta.write('stage4_large_docs', [record], mode='append', async_write=True)
        except Exception as e:
            logger.error(f"Failed to route to Stage 4: {e}")

    def _route_pdf_to_stage4(self, url: str, url_hash: str) -> Dict[str, Any]:
        """Route PDF to Stage 4 and create minimal record."""
        record = {
            'url': url,
            'url_hash': url_hash,
            'text': '',
            'word_count': 0,
            'content_length': 0,
            'status': 'pending',
            'is_pdf': True,
            'queued_at': datetime.now().isoformat(),
        }

        self.delta.write('stage4_large_docs', [record], mode='append', async_write=True)

        return {
            'url': url,
            'url_hash': url_hash,
            'title': 'PDF Document',
            'word_count': 0,
            'is_pdf': True,
            'routed_to_stage4': True,
            'has_error': False,
            'processed_at': datetime.now().isoformat(),
        }

    def _minimal_record(self, url: str, url_hash: str, content_type: str) -> Dict[str, Any]:
        """Create minimal record for non-HTML/PDF content."""
        return {
            'url': url,
            'url_hash': url_hash,
            'title': 'Binary/Other Content',
            'content_type': content_type,
            'word_count': 0,
            'is_low_quality': True,
            'quality_score': 0,
            'has_error': False,
            'processed_at': datetime.now().isoformat(),
        }

    def _error_record(self, url: str, url_hash: str, error_code: int, error_msg: str) -> Dict[str, Any]:
        """Create error record."""
        return {
            'url': url,
            'url_hash': url_hash,
            'title': 'Error',
            'has_error': True,
            'error_code': error_code,
            'error_message': error_msg,
            'word_count': 0,
            'is_low_quality': True,
            'quality_score': 0,
            'processed_at': datetime.now().isoformat(),
        }
