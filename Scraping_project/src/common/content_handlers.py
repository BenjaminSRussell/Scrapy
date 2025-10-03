"""
Content handlers for PDFs, images, videos, and other non-HTML content types.
Supports metadata extraction, text extraction from PDFs, and media processing.
"""

import logging
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ContentHandlerError(Exception):
    """Raised when content handling fails"""
    pass


class PDFHandler:
    """Handler for PDF content extraction"""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize PDF handler with configuration

        Args:
            config: PDF configuration with keys:
                - extract_text: bool
                - extract_metadata: bool
                - max_pages: int
        """
        self.extract_text = config.get('extract_text', True)
        self.extract_metadata = config.get('extract_metadata', True)
        self.max_pages = config.get('max_pages', 100)

        logger.info(f"PDFHandler initialized (extract_text={self.extract_text}, max_pages={self.max_pages})")

    def process_pdf(self, pdf_bytes: bytes, url: str, url_hash: str) -> dict[str, Any]:
        """
        Process PDF content and extract text and metadata

        Args:
            pdf_bytes: Raw PDF bytes
            url: Source URL
            url_hash: URL hash for tracking

        Returns:
            Dict with extracted information
        """
        try:
            # Try PyPDF2 first (more common)
            return self._process_with_pypdf2(pdf_bytes, url, url_hash)
        except ImportError:
            try:
                # Fallback to pdfplumber
                return self._process_with_pdfplumber(pdf_bytes, url, url_hash)
            except ImportError:
                logger.warning("No PDF library available. Install PyPDF2 or pdfplumber: pip install PyPDF2 pdfplumber")
                return self._create_basic_result(pdf_bytes, url, url_hash)

    def _process_with_pypdf2(self, pdf_bytes: bytes, url: str, url_hash: str) -> dict[str, Any]:
        """Process PDF using PyPDF2"""
        import io

        from PyPDF2 import PdfReader

        pdf_file = io.BytesIO(pdf_bytes)
        reader = PdfReader(pdf_file)

        # Extract metadata
        metadata = {}
        if self.extract_metadata and reader.metadata:
            metadata = {
                'title': reader.metadata.get('/Title', ''),
                'author': reader.metadata.get('/Author', ''),
                'subject': reader.metadata.get('/Subject', ''),
                'creator': reader.metadata.get('/Creator', ''),
                'producer': reader.metadata.get('/Producer', ''),
                'creation_date': reader.metadata.get('/CreationDate', ''),
            }

        # Extract text
        text_content = ""
        page_count = len(reader.pages)
        pages_to_process = min(page_count, self.max_pages)

        if self.extract_text:
            text_parts = []
            for i in range(pages_to_process):
                try:
                    page = reader.pages[i]
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                except Exception as e:
                    logger.debug(f"Failed to extract text from page {i}: {e}")

            text_content = "\n".join(text_parts)

        return {
            'url': url,
            'url_hash': url_hash,
            'content_type': 'application/pdf',
            'content_length': len(pdf_bytes),
            'text_content': text_content,
            'word_count': len(text_content.split()) if text_content else 0,
            'page_count': page_count,
            'pages_processed': pages_to_process,
            'metadata': metadata,
            'extracted_at': datetime.now().isoformat(),
        }

    def _process_with_pdfplumber(self, pdf_bytes: bytes, url: str, url_hash: str) -> dict[str, Any]:
        """Process PDF using pdfplumber (alternative library)"""
        import io

        import pdfplumber

        pdf_file = io.BytesIO(pdf_bytes)

        metadata = {}
        text_content = ""
        page_count = 0

        with pdfplumber.open(pdf_file) as pdf:
            page_count = len(pdf.pages)
            pages_to_process = min(page_count, self.max_pages)

            # Extract metadata
            if self.extract_metadata and pdf.metadata:
                metadata = {
                    'title': pdf.metadata.get('Title', ''),
                    'author': pdf.metadata.get('Author', ''),
                    'subject': pdf.metadata.get('Subject', ''),
                    'creator': pdf.metadata.get('Creator', ''),
                    'producer': pdf.metadata.get('Producer', ''),
                }

            # Extract text
            if self.extract_text:
                text_parts = []
                for i in range(pages_to_process):
                    try:
                        page = pdf.pages[i]
                        page_text = page.extract_text()
                        if page_text:
                            text_parts.append(page_text)
                    except Exception as e:
                        logger.debug(f"Failed to extract text from page {i}: {e}")

                text_content = "\n".join(text_parts)

        return {
            'url': url,
            'url_hash': url_hash,
            'content_type': 'application/pdf',
            'content_length': len(pdf_bytes),
            'text_content': text_content,
            'word_count': len(text_content.split()) if text_content else 0,
            'page_count': page_count,
            'pages_processed': pages_to_process,
            'metadata': metadata,
            'extracted_at': datetime.now().isoformat(),
        }

    def _create_basic_result(self, pdf_bytes: bytes, url: str, url_hash: str) -> dict[str, Any]:
        """Create basic result when no PDF library is available"""
        return {
            'url': url,
            'url_hash': url_hash,
            'content_type': 'application/pdf',
            'content_length': len(pdf_bytes),
            'text_content': '',
            'word_count': 0,
            'page_count': 0,
            'pages_processed': 0,
            'metadata': {},
            'extracted_at': datetime.now().isoformat(),
            'warning': 'PDF processing libraries not available'
        }


class MediaHandler:
    """Handler for image, video, and audio content"""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize media handler with configuration

        Args:
            config: Media configuration with keys:
                - extract_metadata: bool
                - download_thumbnails: bool
                - thumbnail_dir: str
        """
        self.extract_metadata = config.get('extract_metadata', True)
        self.download_thumbnails = config.get('download_thumbnails', False)
        self.thumbnail_dir = Path(config.get('thumbnail_dir', 'data/processed/media/thumbnails'))

        if self.download_thumbnails:
            self.thumbnail_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"MediaHandler initialized (extract_metadata={self.extract_metadata})")

    def process_image(self, image_bytes: bytes, url: str, url_hash: str, content_type: str) -> dict[str, Any]:
        """
        Process image content and extract metadata

        Args:
            image_bytes: Raw image bytes
            url: Source URL
            url_hash: URL hash
            content_type: MIME type

        Returns:
            Dict with extracted information
        """
        result = {
            'url': url,
            'url_hash': url_hash,
            'content_type': content_type,
            'content_length': len(image_bytes),
            'metadata': {},
            'extracted_at': datetime.now().isoformat(),
        }

        if not self.extract_metadata:
            return result

        try:
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes))

            result['metadata'] = {
                'format': img.format,
                'mode': img.mode,
                'width': img.width,
                'height': img.height,
                'aspect_ratio': round(img.width / img.height, 2) if img.height > 0 else 0,
            }

            # Extract EXIF data if available
            if hasattr(img, '_getexif') and img._getexif():
                exif_data = img._getexif()
                result['metadata']['exif'] = {k: str(v) for k, v in exif_data.items()}

            # Generate thumbnail if configured
            if self.download_thumbnails:
                thumbnail_path = self._save_thumbnail(img, url_hash)
                result['thumbnail_path'] = str(thumbnail_path)

        except ImportError:
            logger.warning("Pillow not installed. Install with: pip install Pillow")
            result['warning'] = 'Image processing library not available'
        except Exception as e:
            logger.error(f"Failed to process image: {e}")
            result['error'] = str(e)

        return result

    def process_video(self, video_bytes: bytes, url: str, url_hash: str, content_type: str) -> dict[str, Any]:
        """
        Process video content and extract metadata

        Args:
            video_bytes: Raw video bytes
            url: Source URL
            url_hash: URL hash
            content_type: MIME type

        Returns:
            Dict with extracted information
        """
        result = {
            'url': url,
            'url_hash': url_hash,
            'content_type': content_type,
            'content_length': len(video_bytes),
            'metadata': {},
            'extracted_at': datetime.now().isoformat(),
        }

        if not self.extract_metadata:
            return result

        # Video metadata extraction would require ffprobe or similar
        # For now, just return basic info
        logger.debug(f"Video processing not fully implemented. URL: {url}")
        result['warning'] = 'Video metadata extraction not implemented'

        return result

    def process_audio(self, audio_bytes: bytes, url: str, url_hash: str, content_type: str) -> dict[str, Any]:
        """
        Process audio content and extract metadata

        Args:
            audio_bytes: Raw audio bytes
            url: Source URL
            url_hash: URL hash
            content_type: MIME type

        Returns:
            Dict with extracted information
        """
        result = {
            'url': url,
            'url_hash': url_hash,
            'content_type': content_type,
            'content_length': len(audio_bytes),
            'metadata': {},
            'extracted_at': datetime.now().isoformat(),
        }

        if not self.extract_metadata:
            return result

        # Audio metadata extraction would require mutagen or similar
        # For now, just return basic info
        logger.debug(f"Audio processing not fully implemented. URL: {url}")
        result['warning'] = 'Audio metadata extraction not implemented'

        return result

    def _save_thumbnail(self, img, url_hash: str) -> Path:
        """Save thumbnail image"""

        thumbnail_size = (200, 200)
        img_copy = img.copy()
        img_copy.thumbnail(thumbnail_size)

        thumbnail_path = self.thumbnail_dir / f"{url_hash}.jpg"
        img_copy.save(thumbnail_path, "JPEG")

        logger.debug(f"Thumbnail saved: {thumbnail_path}")
        return thumbnail_path


class ContentTypeRouter:
    """Routes content to appropriate handler based on MIME type"""

    def __init__(self, config: dict[str, Any]):
        """
        Initialize content router with configuration

        Args:
            config: Content type configuration with handlers for PDF and media
        """
        self.enabled_types = set(config.get('enabled_types', ['text/html']))
        self.pdf_handler = PDFHandler(config.get('pdf', {}))
        self.media_handler = MediaHandler(config.get('media', {}))

        logger.info(f"ContentTypeRouter initialized with types: {self.enabled_types}")

    def can_process(self, content_type: str) -> bool:
        """Check if content type can be processed"""
        # Normalize content type (remove charset, etc.)
        normalized = content_type.split(';')[0].strip().lower()
        return normalized in self.enabled_types

    def process_content(
        self, content_bytes: bytes, url: str, url_hash: str, content_type: str
    ) -> dict[str, Any]:
        """
        Route content to appropriate handler

        Args:
            content_bytes: Raw content bytes
            url: Source URL
            url_hash: URL hash
            content_type: MIME type

        Returns:
            Dict with processed content information
        """
        # Normalize content type
        normalized = content_type.split(';')[0].strip().lower()

        # Route to appropriate handler
        if normalized == 'application/pdf':
            return self.pdf_handler.process_pdf(content_bytes, url, url_hash)
        elif normalized.startswith('image/'):
            return self.media_handler.process_image(content_bytes, url, url_hash, normalized)
        elif normalized.startswith('video/'):
            return self.media_handler.process_video(content_bytes, url, url_hash, normalized)
        elif normalized.startswith('audio/'):
            return self.media_handler.process_audio(content_bytes, url, url_hash, normalized)
        else:
            raise ContentHandlerError(f"No handler for content type: {normalized}")

    def get_file_extension(self, content_type: str) -> str:
        """Get appropriate file extension for content type"""
        normalized = content_type.split(';')[0].strip().lower()
        extension = mimetypes.guess_extension(normalized)
        return extension or '.bin'
