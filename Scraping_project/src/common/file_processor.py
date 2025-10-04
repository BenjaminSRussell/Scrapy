"""
File content processor for extracting text from various file types.
Supports OCR for images and Whisper for audio files.
"""

import logging
import mimetypes
import tempfile
from pathlib import Path
from typing import Optional, Union

import pytesseract
import torch
import whisper
from PIL import Image
from transformers import pipeline

# Setup logging
logger = logging.getLogger(__name__)

# Supported file types and their MIME types
SUPPORTED_IMAGE_TYPES = {
    'image/jpeg', 'image/png', 'image/tiff', 'image/gif', 'image/bmp'
}

SUPPORTED_AUDIO_TYPES = {
    'audio/mpeg', 'audio/wav', 'audio/ogg', 'audio/x-wav', 'audio/x-m4a'
}

class FileProcessor:
    """Process different file types to extract text content."""
    
    def __init__(self):
        """Initialize file processor with required models."""
        self.whisper_model = None  # Lazy load
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        logger.info(f"Using device: {self.device}")
        
        # Initialize OCR pipeline
        try:
            pytesseract.get_tesseract_version()
            logger.info("Tesseract OCR initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Tesseract OCR: {e}")
            raise
            
    def _load_whisper_model(self):
        """Lazy load Whisper model."""
        if self.whisper_model is None:
            logger.info("Loading Whisper model...")
            try:
                self.whisper_model = whisper.load_model("base")
                logger.info("Whisper model loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load Whisper model: {e}")
                raise

    def detect_file_type(self, file_path: Union[str, Path], content_type: Optional[str] = None) -> str:
        """Detect file type from path and optional content type header."""
        file_path = Path(file_path)
        
        # Try content type first if provided
        if content_type:
            mime_type = content_type.split(';')[0].lower()
            if mime_type in SUPPORTED_IMAGE_TYPES or mime_type in SUPPORTED_AUDIO_TYPES:
                return mime_type
                
        # Fall back to file extension
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            return mime_type.lower()
            
        raise ValueError(f"Unsupported or unknown file type: {file_path}")

    def extract_text_from_image(self, image_path: Union[str, Path]) -> str:
        """Extract text from image using OCR."""
        logger.info(f"Processing image: {image_path}")
        
        try:
            # Open and process image
            image = Image.open(image_path)
            
            # Convert to RGB if necessary
            if image.mode != 'RGB':
                image = image.convert('RGB')
                
            # Perform OCR
            text = pytesseract.image_to_string(image)
            
            if not text.strip():
                logger.warning(f"No text extracted from image: {image_path}")
                return ""
                
            logger.info(f"Successfully extracted {len(text)} chars from image")
            return text.strip()
            
        except Exception as e:
            logger.error(f"Failed to process image {image_path}: {e}")
            return ""

    def extract_text_from_audio(self, audio_path: Union[str, Path]) -> str:
        """Extract text from audio using Whisper."""
        logger.info(f"Processing audio: {audio_path}")
        
        try:
            # Lazy load model
            self._load_whisper_model()
            
            # Transcribe audio
            result = self.whisper_model.transcribe(str(audio_path))
            text = result["text"]
            
            if not text.strip():
                logger.warning(f"No text extracted from audio: {audio_path}")
                return ""
                
            logger.info(f"Successfully extracted {len(text)} chars from audio")
            return text.strip()
            
        except Exception as e:
            logger.error(f"Failed to process audio {audio_path}: {e}")
            return ""

    def process_file(self, file_path: Union[str, Path], content_type: Optional[str] = None) -> str:
        """Process a file and extract text content based on its type."""
        file_path = Path(file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
            
        mime_type = self.detect_file_type(file_path, content_type)
        
        if mime_type in SUPPORTED_IMAGE_TYPES:
            return self.extract_text_from_image(file_path)
        elif mime_type in SUPPORTED_AUDIO_TYPES:
            return self.extract_text_from_audio(file_path)
        else:
            raise ValueError(f"Unsupported mime type: {mime_type}")

    def process_url_content(self, content: bytes, content_type: str) -> str:
        """Process content from a URL with known content type."""
        mime_type = content_type.split(';')[0].lower()
        
        if mime_type not in SUPPORTED_IMAGE_TYPES and mime_type not in SUPPORTED_AUDIO_TYPES:
            raise ValueError(f"Unsupported content type: {content_type}")
            
        # Create temporary file
        suffix = mimetypes.guess_extension(mime_type) or '.tmp'
        
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            try:
                # Write content to temp file
                tmp.write(content)
                tmp.flush()
                
                # Process the temp file
                return self.process_file(tmp.name, content_type)
            finally:
                # Clean up
                Path(tmp.name).unlink(missing_ok=True)