"""
Stage 2: File Type Detection, Validation, OCR, Whisper, and Metadata Extraction

Responsibilities:
- Validate URLs are accessible
- Detect file types (HTML, PDF, image, audio, video)
- Extract metadata (content-type, size, last-modified)
- Run OCR on PDFs and images
- Transcribe audio with Whisper
- Categorize and label content
- Write structured results to Delta Lake
"""

import asyncio
import hashlib
import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from src.common.constants import DELTA_VALIDATED_URLS
from src.common.delta_lake import DeltaLakeWriter, DeltaLakeReader, DELTA_RAW_URLS
from src.common.file_processors import (
    get_file_type,
    extract_text_from_pdf,
    extract_text_from_image,
    transcribe_audio
)
from src.common.logging import get_logger
from src.common.summarization import summarize_text

logger = get_logger(__name__)


class FileValidator:
    """Validate URLs and process different file types."""

    def __init__(
        self,
        concurrency: int = 50,
        timeout: int = 30,
        enable_ocr: bool = True,
        enable_whisper: bool = True
    ):
        self.concurrency = concurrency
        self.timeout = timeout
        self.enable_ocr = enable_ocr
        self.enable_whisper = enable_whisper

        # Stats
        self.stats = {
            'total': 0,
            'valid': 0,
            'invalid': 0,
            'pdf': 0,
            'image': 0,
            'audio': 0,
            'video': 0,
            'html': 0,
            'ocr_processed': 0,
            'whisper_processed': 0
        }

        # Batch buffer
        self.batch_buffer = []
        self.batch_size = 100

        # Delta Lake
        self.delta_writer = DeltaLakeWriter(
            DELTA_VALIDATED_URLS,
            partition_by=['validation_date', 'file_type']
        )

    async def _validate_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
        url_hash: str
    ) -> dict:
        """Validate URL and extract metadata."""

        result = {
            'url': url,
            'url_hash': url_hash,
            'is_valid': False,
            'status_code': None,
            'content_type': None,
            'content_length': None,
            'last_modified': None,
            'file_type': 'unknown',
            'file_extension': None,
            'error_message': None,
            'validation_date': datetime.now().strftime('%Y-%m-%d'),
            'validation_timestamp': datetime.now().isoformat(),

            # File-specific fields
            'extracted_text': None,
            'text_preview': None,
            'processing_method': None,
            'requires_enrichment': True,

            # Metadata
            'domain': None,
            'path': None,
            'is_binary': False,
            'is_media': False,
            'is_document': False
        }

        try:
            # Parse URL
            parsed = urlparse(url)
            result['domain'] = parsed.netloc
            result['path'] = parsed.path
            result['file_extension'] = Path(parsed.path).suffix.lower()

            # Try HEAD first
            async with session.head(url, allow_redirects=True) as response:
                result['status_code'] = response.status
                result['is_valid'] = 200 <= response.status < 300

                if not result['is_valid']:
                    result['error_message'] = f"HTTP {response.status}"
                    return result

                # Extract metadata from headers
                result['content_type'] = response.headers.get('Content-Type', '')
                result['content_length'] = response.headers.get('Content-Length')
                result['last_modified'] = response.headers.get('Last-Modified')

                # Detect file type
                file_type = get_file_type(url)
                result['file_type'] = file_type

                # Categorize
                result['is_binary'] = file_type in ('pdf', 'image', 'audio', 'video')
                result['is_media'] = file_type in ('audio', 'video', 'image')
                result['is_document'] = file_type in ('pdf', 'document')

                self.stats[file_type] = self.stats.get(file_type, 0) + 1

                # Process based on file type
                if file_type == 'pdf' and self.enable_ocr:
                    await self._process_pdf(session, url, result)

                elif file_type == 'image' and self.enable_ocr:
                    await self._process_image(session, url, result)

                elif file_type == 'audio' and self.enable_whisper:
                    await self._process_audio(session, url, result)

                elif file_type == 'video':
                    result['requires_enrichment'] = False  # Stage 3 handles video

                elif file_type == 'html':
                    # HTML goes to Stage 3 for full processing
                    result['requires_enrichment'] = True

        except asyncio.TimeoutError:
            result['error_message'] = "Timeout"
            result['is_valid'] = False

        except aiohttp.ClientError as e:
            result['error_message'] = f"Client error: {str(e)[:100]}"
            result['is_valid'] = False

        except Exception as e:
            result['error_message'] = f"Error: {str(e)[:100]}"
            result['is_valid'] = False
            logger.error(f"Validation error for {url}: {e}")

        return result

    async def _process_pdf(
        self,
        session: aiohttp.ClientSession,
        url: str,
        result: dict
    ):
        """Extract text from PDF using OCR."""
        try:
            logger.info(f"Processing PDF: {url}")

            # Download PDF
            async with session.get(url) as response:
                if response.status != 200:
                    return

                pdf_data = await response.read()

                # Save temporarily
                temp_path = Path("/tmp") / f"temp_{result['url_hash']}.pdf"
                temp_path.write_bytes(pdf_data)

                # Extract text
                text = extract_text_from_pdf(str(temp_path))

                if text:
                    result['extracted_text'] = text
                    result['text_preview'] = text[:500]
                    result['processing_method'] = 'pdf_ocr'
                    result['requires_enrichment'] = False  # Already processed
                    self.stats['ocr_processed'] += 1

                # Cleanup
                temp_path.unlink(missing_ok=True)

        except Exception as e:
            logger.error(f"PDF processing failed for {url}: {e}")

    async def _process_image(
        self,
        session: aiohttp.ClientSession,
        url: str,
        result: dict
    ):
        """Extract text from image using OCR."""
        try:
            logger.info(f"Processing image: {url}")

            # Download image
            async with session.get(url) as response:
                if response.status != 200:
                    return

                image_data = await response.read()

                # Save temporarily
                ext = result['file_extension'] or '.jpg'
                temp_path = Path("/tmp") / f"temp_{result['url_hash']}{ext}"
                temp_path.write_bytes(image_data)

                # OCR
                text = extract_text_from_image(str(temp_path))

                if text:
                    result['extracted_text'] = text
                    result['text_preview'] = text[:500]
                    result['processing_method'] = 'ocr'
                    result['requires_enrichment'] = False
                    self.stats['ocr_processed'] += 1

                # Cleanup
                temp_path.unlink(missing_ok=True)

        except Exception as e:
            logger.error(f"Image OCR failed for {url}: {e}")

    async def _process_audio(
        self,
        session: aiohttp.ClientSession,
        url: str,
        result: dict
    ):
        """Transcribe audio using Whisper."""
        try:
            logger.info(f"Processing audio: {url}")

            # Download audio
            async with session.get(url) as response:
                if response.status != 200:
                    return

                audio_data = await response.read()

                # Save temporarily
                ext = result['file_extension'] or '.mp3'
                temp_path = Path("/tmp") / f"temp_{result['url_hash']}{ext}"
                temp_path.write_bytes(audio_data)

                # Transcribe
                transcript = transcribe_audio(str(temp_path))

                if transcript:
                    result['extracted_text'] = transcript
                    result['text_preview'] = transcript[:500]
                    result['processing_method'] = 'whisper'
                    result['requires_enrichment'] = False
                    self.stats['whisper_processed'] += 1

                # Cleanup
                temp_path.unlink(missing_ok=True)

        except Exception as e:
            logger.error(f"Whisper transcription failed for {url}: {e}")

    async def _flush_batch(self):
        """Write batch to Delta Lake."""
        if not self.batch_buffer:
            return

        try:
            self.delta_writer.write(self.batch_buffer, mode='append')
            logger.info(
                f"Wrote {len(self.batch_buffer)} validated URLs to Delta Lake "
                f"(valid: {self.stats['valid']}, invalid: {self.stats['invalid']})"
            )
            self.batch_buffer = []
        except Exception as e:
            logger.error(f"Delta Lake write failed: {e}")

    async def run(self):
        """Run validation on all discovered URLs."""
        logger.info("Starting Stage 2: File Validation & Processing")

        # Read URLs from Stage 1 Delta Lake
        logger.info("Reading URLs from Delta Lake (Stage 1 output)...")
        reader = DeltaLakeReader(DELTA_RAW_URLS)
        urls_data = reader.read(columns=['url', 'url_hash'])

        logger.info(f"Found {len(urls_data)} URLs to validate")

        start_time = datetime.now()

        # Create session
        connector = aiohttp.TCPConnector(limit=self.concurrency)
        timeout = aiohttp.ClientTimeout(total=self.timeout)

        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        ) as session:

            # Process in batches
            for i in range(0, len(urls_data), self.concurrency):
                batch = urls_data[i:i + self.concurrency]

                tasks = [
                    self._validate_url(
                        session,
                        item['url'],
                        item['url_hash']
                    )
                    for item in batch
                ]

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Add to buffer
                for result in results:
                    if isinstance(result, dict):
                        self.batch_buffer.append(result)
                        self.stats['total'] += 1

                        if result['is_valid']:
                            self.stats['valid'] += 1
                        else:
                            self.stats['invalid'] += 1

                # Flush if buffer full
                if len(self.batch_buffer) >= self.batch_size:
                    await self._flush_batch()

                # Progress
                if (i + len(batch)) % 500 == 0:
                    logger.info(f"Processed {i + len(batch)}/{len(urls_data)} URLs")

            # Flush remaining
            await self._flush_batch()

        elapsed = (datetime.now() - start_time).total_seconds()

        logger.info("=" * 60)
        logger.info("STAGE 2 COMPLETE")
        logger.info(f"Total processed: {self.stats['total']}")
        logger.info(f"Valid: {self.stats['valid']}")
        logger.info(f"Invalid: {self.stats['invalid']}")
        logger.info(f"HTML pages: {self.stats.get('html', 0)}")
        logger.info(f"PDFs: {self.stats.get('pdf', 0)}")
        logger.info(f"Images: {self.stats.get('image', 0)}")
        logger.info(f"Audio files: {self.stats.get('audio', 0)}")
        logger.info(f"OCR processed: {self.stats['ocr_processed']}")
        logger.info(f"Whisper processed: {self.stats['whisper_processed']}")
        logger.info(f"Time: {elapsed:.1f}s")
        logger.info(f"Rate: {self.stats['total'] / elapsed:.1f} URLs/sec")
        logger.info("=" * 60)


async def run_validation(
    concurrency: int = 50,
    timeout: int = 30,
    enable_ocr: bool = True,
    enable_whisper: bool = True
):
    """Main entry point."""
    validator = FileValidator(
        concurrency=concurrency,
        timeout=timeout,
        enable_ocr=enable_ocr,
        enable_whisper=enable_whisper
    )

    await validator.run()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description='Stage 2: File Validation & Processing')
    parser.add_argument('--concurrency', type=int, default=50)
    parser.add_argument('--timeout', type=int, default=30)
    parser.add_argument('--no-ocr', action='store_true', help='Disable OCR')
    parser.add_argument('--no-whisper', action='store_true', help='Disable Whisper')

    args = parser.parse_args()

    asyncio.run(run_validation(
        concurrency=args.concurrency,
        timeout=args.timeout,
        enable_ocr=not args.no_ocr,
        enable_whisper=not args.no_whisper
    ))


if __name__ == '__main__':
    main()
