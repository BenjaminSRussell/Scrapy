"""
Media Extraction Module

Provides OCR for images, video/audio transcription capabilities,
and media content analysis for Stage 3 enrichment.
"""

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Optional imports - gracefully handle missing dependencies
try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.info("Tesseract OCR not available. Install: pip install pytesseract pillow")

try:
    import speech_recognition as sr
    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    SPEECH_RECOGNITION_AVAILABLE = False
    logger.info("Speech recognition not available. Install: pip install SpeechRecognition")


@dataclass
class OCRResult:
    """OCR extraction result"""
    text: str = ""
    confidence: float = 0.0
    language: str = "eng"
    word_count: int = 0

    # Metadata
    image_url: str = ""
    image_hash: str = ""
    image_dimensions: tuple | None = None  # (width, height)

    # Error handling
    success: bool = False
    error_message: str | None = None


@dataclass
class TranscriptionResult:
    """Audio/video transcription result"""
    text: str = ""
    confidence: float = 0.0
    language: str = "en-US"
    duration_seconds: float | None = None

    # Metadata
    media_url: str = ""
    media_hash: str = ""
    media_type: str = ""  # audio, video

    # Timestamps (if available)
    segments: list[dict[str, Any]] = field(default_factory=list)

    # Error handling
    success: bool = False
    error_message: str | None = None


class ImageOCRExtractor:
    """Extract text from images using OCR"""

    def __init__(self, config: dict | None = None):
        """
        Initialize OCR extractor.

        Args:
            config: Optional configuration dict with:
                - languages: List of languages to detect (default: ['eng'])
                - min_confidence: Minimum confidence threshold (default: 60)
                - preprocessing: Enable image preprocessing (default: True)
        """
        self.config = config or {}
        self.languages = self.config.get('languages', ['eng'])
        self.min_confidence = self.config.get('min_confidence', 60)
        self.preprocessing = self.config.get('preprocessing', True)

        if not TESSERACT_AVAILABLE:
            logger.warning("Tesseract OCR not available. OCR extraction disabled.")

    def extract_text_from_image(
        self,
        image_data: bytes,
        image_url: str,
        mime_type: str = "image/jpeg"
    ) -> OCRResult:
        """
        Extract text from image using OCR.

        Args:
            image_data: Raw image bytes
            image_url: Source URL of the image
            mime_type: Image MIME type

        Returns:
            OCRResult with extracted text and metadata
        """
        if not TESSERACT_AVAILABLE:
            return OCRResult(
                success=False,
                error_message="Tesseract OCR not available",
                image_url=image_url
            )

        try:
            # Generate image hash
            image_hash = hashlib.sha256(image_data).hexdigest()

            # Open image from bytes
            from io import BytesIO
            image = Image.open(BytesIO(image_data))

            # Get dimensions
            width, height = image.size

            # Preprocess image if enabled
            if self.preprocessing:
                image = self._preprocess_image(image)

            # Perform OCR
            lang_str = '+'.join(self.languages)
            text = pytesseract.image_to_string(image, lang=lang_str)

            # Get confidence scores (if available)
            try:
                data = pytesseract.image_to_data(image, lang=lang_str, output_type=pytesseract.Output.DICT)
                confidences = [int(conf) for conf in data['conf'] if conf != '-1']
                avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            except Exception:
                avg_confidence = 0

            # Clean and analyze text
            text = text.strip()
            word_count = len(text.split())

            # Check if extraction was successful
            success = word_count > 0 and avg_confidence >= self.min_confidence

            return OCRResult(
                text=text,
                confidence=round(avg_confidence, 2),
                language=self.languages[0],
                word_count=word_count,
                image_url=image_url,
                image_hash=image_hash,
                image_dimensions=(width, height),
                success=success
            )

        except Exception as e:
            logger.error(f"OCR extraction failed for {image_url}: {e}")
            return OCRResult(
                success=False,
                error_message=str(e),
                image_url=image_url
            )

    def _preprocess_image(self, image: 'Image.Image') -> 'Image.Image':
        """
        Preprocess image to improve OCR accuracy.

        Applies grayscale conversion, contrast enhancement, etc.
        """
        try:
            # Convert to grayscale
            if image.mode != 'L':
                image = image.convert('L')

            # Enhance contrast
            from PIL import ImageEnhance
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(2)

            # Resize if too small (OCR works better with larger images)
            width, height = image.size
            if width < 300 or height < 300:
                scale = max(300 / width, 300 / height)
                new_size = (int(width * scale), int(height * scale))
                image = image.resize(new_size, Image.Resampling.LANCZOS)

            return image
        except Exception as e:
            logger.warning(f"Image preprocessing failed: {e}")
            return image


class MediaTranscriber:
    """Transcribe audio and video content"""

    def __init__(self, config: dict | None = None):
        """
        Initialize media transcriber.

        Args:
            config: Optional configuration dict with:
                - engine: Transcription engine (google, sphinx, wit, etc.)
                - language: Language code (default: en-US)
                - api_key: API key for cloud services
        """
        self.config = config or {}
        self.engine = self.config.get('engine', 'google')  # Default to Google
        self.language = self.config.get('language', 'en-US')
        self.api_key = self.config.get('api_key')

        if not SPEECH_RECOGNITION_AVAILABLE:
            logger.warning("Speech recognition not available. Transcription disabled.")

        self.recognizer = sr.Recognizer() if SPEECH_RECOGNITION_AVAILABLE else None

    def transcribe_audio(
        self,
        audio_data: bytes,
        media_url: str,
        media_type: str = "audio/mpeg"
    ) -> TranscriptionResult:
        """
        Transcribe audio content to text.

        Args:
            audio_data: Raw audio bytes
            media_url: Source URL of the audio
            media_type: Audio MIME type

        Returns:
            TranscriptionResult with transcription and metadata
        """
        if not SPEECH_RECOGNITION_AVAILABLE:
            return TranscriptionResult(
                success=False,
                error_message="Speech recognition not available",
                media_url=media_url,
                media_type=media_type
            )

        try:
            # Generate media hash
            media_hash = hashlib.sha256(audio_data).hexdigest()

            # Convert audio data to AudioFile format
            import tempfile

            # Save to temporary file (speech_recognition requires file)
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                temp_path = temp_file.name
                # Note: In production, convert audio_data to WAV format first
                temp_file.write(audio_data)

            # Load audio file
            try:
                with sr.AudioFile(temp_path) as source:
                    audio = self.recognizer.record(source)
                    duration = source.DURATION if hasattr(source, 'DURATION') else None
            finally:
                # Clean up temp file
                Path(temp_path).unlink(missing_ok=True)

            # Perform transcription based on engine
            text = self._transcribe_with_engine(audio)

            # Calculate word count
            len(text.split())

            return TranscriptionResult(
                text=text,
                confidence=0.8,  # Engines don't always provide confidence
                language=self.language,
                duration_seconds=duration,
                media_url=media_url,
                media_hash=media_hash,
                media_type=media_type,
                success=True
            )

        except Exception as e:
            logger.error(f"Audio transcription failed for {media_url}: {e}")
            return TranscriptionResult(
                success=False,
                error_message=str(e),
                media_url=media_url,
                media_type=media_type
            )

    def transcribe_video(
        self,
        video_data: bytes,
        media_url: str,
        media_type: str = "video/mp4"
    ) -> TranscriptionResult:
        """
        Transcribe video content (extract audio track and transcribe).

        Args:
            video_data: Raw video bytes
            media_url: Source URL of the video
            media_type: Video MIME type

        Returns:
            TranscriptionResult with transcription and metadata
        """
        # Note: In production, use ffmpeg or moviepy to extract audio track
        # For now, return placeholder

        logger.info(f"Video transcription requested for {media_url}")

        return TranscriptionResult(
            success=False,
            error_message="Video transcription requires audio extraction (implement with ffmpeg)",
            media_url=media_url,
            media_type=media_type
        )

    def _transcribe_with_engine(self, audio_data: sr.AudioData) -> str:
        """
        Transcribe audio using configured engine.

        Args:
            audio_data: AudioData object from speech_recognition

        Returns:
            Transcribed text
        """
        if self.engine == 'google':
            return self.recognizer.recognize_google(audio_data, language=self.language)
        elif self.engine == 'sphinx':
            return self.recognizer.recognize_sphinx(audio_data)
        elif self.engine == 'wit':
            if not self.api_key:
                raise ValueError("Wit.ai requires API key")
            return self.recognizer.recognize_wit(audio_data, key=self.api_key)
        elif self.engine == 'azure':
            if not self.api_key:
                raise ValueError("Azure requires API key")
            return self.recognizer.recognize_azure(audio_data, key=self.api_key)
        else:
            raise ValueError(f"Unsupported transcription engine: {self.engine}")


class MediaAnalyzer:
    """Unified interface for media content analysis"""

    def __init__(self, config: dict | None = None):
        """
        Initialize media analyzer with OCR and transcription capabilities.

        Args:
            config: Configuration dict with ocr and transcription settings
        """
        self.config = config or {}

        ocr_config = self.config.get('ocr', {})
        transcription_config = self.config.get('transcription', {})

        self.ocr_extractor = ImageOCRExtractor(ocr_config)
        self.transcriber = MediaTranscriber(transcription_config)

    def process_image(
        self,
        image_data: bytes,
        image_url: str,
        mime_type: str = "image/jpeg"
    ) -> OCRResult:
        """Extract text from image using OCR"""
        return self.ocr_extractor.extract_text_from_image(image_data, image_url, mime_type)

    def process_audio(
        self,
        audio_data: bytes,
        media_url: str,
        media_type: str = "audio/mpeg"
    ) -> TranscriptionResult:
        """Transcribe audio to text"""
        return self.transcriber.transcribe_audio(audio_data, media_url, media_type)

    def process_video(
        self,
        video_data: bytes,
        media_url: str,
        media_type: str = "video/mp4"
    ) -> TranscriptionResult:
        """Transcribe video to text"""
        return self.transcriber.transcribe_video(video_data, media_url, media_type)
