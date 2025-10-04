"""
File processors for PDFs, images, audio, and video.

Handles OCR (EasyOCR/Tesseract), audio transcription (Whisper), and content extraction.
"""

import logging
import mimetypes
from pathlib import Path
from typing import Optional

from src.common.constants import (
    OCR_ENGINE,
    OCR_LANGUAGES,
    WHISPER_MODEL,
    SUPPORTED_IMAGE_TYPES,
    SUPPORTED_AUDIO_TYPES,
    SUPPORTED_DOC_TYPES,
    MAX_IMAGE_SIZE_MB
)
from src.common.summarization import (
    summarize_pdf_content,
    summarize_audio_transcript
)

logger = logging.getLogger(__name__)

# Lazy load heavy dependencies
_ocr_reader = None
_whisper_model = None


def get_file_type(url: str) -> str:
    """Detect file type from URL or content type."""
    url_lower = url.lower()

    # Check by extension
    if any(url_lower.endswith(ext) for ext in SUPPORTED_IMAGE_TYPES):
        return "image"
    if any(url_lower.endswith(ext) for ext in SUPPORTED_AUDIO_TYPES):
        return "audio"
    if any(url_lower.endswith(ext) for ext in SUPPORTED_DOC_TYPES):
        return "document"
    if url_lower.endswith('.pdf'):
        return "pdf"

    # Check by mime type
    mime_type, _ = mimetypes.guess_type(url)
    if mime_type:
        if mime_type.startswith('image/'):
            return "image"
        if mime_type.startswith('audio/'):
            return "audio"
        if mime_type.startswith('video/'):
            return "video"
        if mime_type == 'application/pdf':
            return "pdf"

    return "html"  # Default


def get_ocr_reader():
    """Lazy load OCR engine."""
    global _ocr_reader

    if _ocr_reader is None:
        if OCR_ENGINE == "easyocr":
            try:
                import easyocr
                logger.info(f"Loading EasyOCR with languages: {OCR_LANGUAGES}")
                _ocr_reader = easyocr.Reader(OCR_LANGUAGES, gpu=False)
                logger.info("EasyOCR loaded successfully")
            except ImportError:
                logger.error("EasyOCR not installed: pip install easyocr")
                raise
        elif OCR_ENGINE == "tesseract":
            try:
                import pytesseract
                _ocr_reader = pytesseract
                logger.info("Tesseract OCR loaded successfully")
            except ImportError:
                logger.error("pytesseract not installed: pip install pytesseract")
                raise
        else:
            raise ValueError(f"Unknown OCR engine: {OCR_ENGINE}")

    return _ocr_reader


def get_whisper_model():
    """Lazy load Whisper model."""
    global _whisper_model

    if _whisper_model is None:
        try:
            import whisper
            logger.info(f"Loading Whisper model: {WHISPER_MODEL}")
            _whisper_model = whisper.load_model(WHISPER_MODEL)
            logger.info("Whisper model loaded successfully")
        except ImportError:
            logger.error("Whisper not installed: pip install openai-whisper")
            raise

    return _whisper_model


def extract_text_from_image(image_path: str) -> Optional[str]:
    """
    Extract text from image using OCR.

    Args:
        image_path: Path to image file or URL

    Returns:
        Extracted text or None
    """
    try:
        reader = get_ocr_reader()

        if OCR_ENGINE == "easyocr":
            result = reader.readtext(image_path, detail=0)
            text = " ".join(result)
        elif OCR_ENGINE == "tesseract":
            from PIL import Image
            import requests
            from io import BytesIO

            # Load image
            if image_path.startswith('http'):
                response = requests.get(image_path, timeout=30)
                img = Image.open(BytesIO(response.content))
            else:
                img = Image.open(image_path)

            text = reader.image_to_string(img)
        else:
            return None

        logger.info(f"OCR extracted {len(text)} characters from {image_path}")
        return text.strip()

    except Exception as e:
        logger.error(f"OCR failed for {image_path}: {e}")
        return None


def extract_text_from_pdf(pdf_path: str, use_ocr: bool = True) -> Optional[str]:
    """
    Extract text from PDF.

    Args:
        pdf_path: Path to PDF file or URL
        use_ocr: Whether to use OCR for scanned PDFs

    Returns:
        Extracted text with summary
    """
    try:
        import PyPDF2
        import requests
        from io import BytesIO

        # Load PDF
        if pdf_path.startswith('http'):
            response = requests.get(pdf_path, timeout=30)
            pdf_file = BytesIO(response.content)
        else:
            pdf_file = open(pdf_path, 'rb')

        # Extract text
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text_parts = []

        for page in pdf_reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)

        full_text = "\n".join(text_parts)

        # If no text extracted and OCR enabled, try OCR
        if len(full_text.strip()) < 50 and use_ocr:
            logger.info(f"PDF has minimal text, attempting OCR: {pdf_path}")
            # Convert PDF to images and OCR each page
            # TODO: Implement pdf2image + OCR pipeline
            pass

        # Summarize if text is long
        if len(full_text.split()) > 500:
            summary = summarize_pdf_content(full_text)
            logger.info(f"Summarized PDF {pdf_path}: {len(full_text)} -> {len(summary)} chars")
            return summary

        return full_text.strip()

    except Exception as e:
        logger.error(f"PDF extraction failed for {pdf_path}: {e}")
        return None


def transcribe_audio(audio_path: str, language: str = 'en') -> Optional[str]:
    """
    Transcribe audio using Whisper.

    Args:
        audio_path: Path to audio file or URL
        language: Language code (e.g., 'en', 'es')

    Returns:
        Transcript with summary
    """
    try:
        import requests
        from io import BytesIO

        model = get_whisper_model()

        # Download if URL
        if audio_path.startswith('http'):
            response = requests.get(audio_path, timeout=60)
            temp_path = Path("/tmp") / "temp_audio.mp3"
            temp_path.write_bytes(response.content)
            audio_path = str(temp_path)

        # Transcribe
        result = model.transcribe(audio_path, language=language)
        transcript = result['text']

        logger.info(f"Transcribed audio {audio_path}: {len(transcript)} chars")

        # Summarize if long
        if len(transcript.split()) > 200:
            summary = summarize_audio_transcript(transcript)
            return summary

        return transcript.strip()

    except Exception as e:
        logger.error(f"Audio transcription failed for {audio_path}: {e}")
        return None


def process_file(url: str, content_type: Optional[str] = None) -> dict:
    """
    Process file based on type and extract content.

    Args:
        url: URL or path to file
        content_type: MIME type (optional)

    Returns:
        Dictionary with extracted content and metadata
    """
    file_type = get_file_type(url)

    result = {
        'url': url,
        'file_type': file_type,
        'extracted_text': None,
        'summary': None,
        'processing_method': None,
        'error': None
    }

    try:
        if file_type == 'pdf':
            text = extract_text_from_pdf(url)
            result['extracted_text'] = text
            result['processing_method'] = 'pdf_extraction'

        elif file_type == 'image':
            text = extract_text_from_image(url)
            result['extracted_text'] = text
            result['processing_method'] = 'ocr'

        elif file_type == 'audio':
            transcript = transcribe_audio(url)
            result['extracted_text'] = transcript
            result['processing_method'] = 'whisper'

        elif file_type == 'document':
            # Handle .doc, .docx, etc.
            # TODO: Implement document parsing
            logger.warning(f"Document type not yet supported: {url}")
            result['processing_method'] = 'unsupported'

        else:
            result['processing_method'] = 'html'

    except Exception as e:
        result['error'] = str(e)
        logger.error(f"File processing failed for {url}: {e}")

    return result


def detect_content_types(url: str, html_content: str = "") -> list[str]:
    """
    Detect content types present in URL or HTML.

    Args:
        url: URL to check
        html_content: HTML content to scan for embedded media

    Returns:
        List of content types found
    """
    content_types = []

    # Check URL file type
    file_type = get_file_type(url)
    if file_type != 'html':
        content_types.append(file_type)

    # Scan HTML for embedded content
    if html_content:
        if any(ext in html_content.lower() for ext in SUPPORTED_AUDIO_TYPES):
            content_types.append('embedded_audio')

        if '<video' in html_content.lower():
            content_types.append('embedded_video')

        if any(ext in html_content.lower() for ext in SUPPORTED_IMAGE_TYPES):
            content_types.append('embedded_images')

        if '.pdf' in html_content.lower():
            content_types.append('embedded_pdf')

    return list(set(content_types))
