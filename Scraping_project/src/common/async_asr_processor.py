"""Asynchronous Automatic Speech Recognition (ASR) processor for media transcription.

This module provides asynchronous ASR processing for audio/video files to prevent
blocking the Scrapy reactor. It uses process pools and Twisted Deferreds to maintain
high scraping throughput while performing CPU-intensive transcription tasks.
"""

import logging
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    requests = None

try:
    import speech_recognition as sr

    SPEECH_RECOGNITION_AVAILABLE = True
except ImportError:
    SPEECH_RECOGNITION_AVAILABLE = False
    sr = None

try:
    from twisted.internet import defer, threads

    TWISTED_AVAILABLE = True
except ImportError:
    TWISTED_AVAILABLE = False
    defer = None
    threads = None

logger = logging.getLogger(__name__)


def transcribe_audio_file(audio_path: str, language: str = "en-US") -> dict[str, Any]:
    """Transcribe audio file using SpeechRecognition library.

    This function runs in a separate process to avoid blocking the main event loop.

    Args:
        audio_path: Path to audio file (WAV, FLAC, etc.)
        language: Language code for recognition (default: en-US)

    Returns:
        Dictionary with keys:
            - success: Boolean indicating if transcription succeeded
            - transcript: Transcribed text (if successful)
            - error: Error message (if failed)
            - duration: Approximate duration in seconds (if available)

    Examples:
        >>> result = transcribe_audio_file("/path/to/audio.wav")
        >>> if result['success']:
        ...     print(result['transcript'])
    """
    if not SPEECH_RECOGNITION_AVAILABLE:
        return {
            "success": False,
            "transcript": "",
            "error": "speech_recognition library not available",
            "duration": 0,
        }

    recognizer = sr.Recognizer()

    try:
        with sr.AudioFile(audio_path) as source:
            # Record the audio data
            audio_data = recognizer.record(source)

            # Calculate approximate duration (samples / sample_rate)
            duration = len(audio_data.frame_data) / audio_data.sample_rate

            # Perform speech recognition using Google Speech Recognition
            # Note: This requires internet connection
            # For offline ASR, consider using: recognizer.recognize_sphinx(audio_data)
            transcript = recognizer.recognize_google(audio_data, language=language)

            return {
                "success": True,
                "transcript": transcript,
                "error": None,
                "duration": duration,
            }

    except sr.UnknownValueError:
        logger.warning(f"Could not understand audio in {audio_path}")
        return {
            "success": False,
            "transcript": "",
            "error": "Could not understand audio",
            "duration": 0,
        }
    except sr.RequestError as e:
        logger.error(f"ASR service error for {audio_path}: {e}")
        return {
            "success": False,
            "transcript": "",
            "error": f"ASR service error: {e}",
            "duration": 0,
        }
    except Exception as e:
        logger.error(f"Unexpected error transcribing {audio_path}: {e}")
        return {
            "success": False,
            "transcript": "",
            "error": f"Unexpected error: {e}",
            "duration": 0,
        }


class AsyncASRProcessor:
    """Asynchronous ASR processor using process pools and Twisted Deferreds.

    This processor ensures that CPU/GPU-intensive ASR tasks do not block the
    Scrapy reactor, maintaining high scraping throughput. It downloads media
    files, transcribes them asynchronously, and returns results via Deferreds.

    Architecture:
        - Downloads media files to temporary directory
        - Submits transcription jobs to ProcessPoolExecutor
        - Uses Twisted Deferreds to integrate with Scrapy's async architecture
        - Cleans up temporary files automatically

    Configuration:
        MAX_WORKERS: Maximum number of parallel transcription processes
        TEMP_DIR: Directory for temporary media files
        SUPPORTED_FORMATS: Audio/video formats to process
    """

    SUPPORTED_AUDIO_FORMATS = {".wav", ".flac", ".aiff", ".mp3", ".ogg"}
    SUPPORTED_VIDEO_FORMATS = {".mp4", ".avi", ".mov", ".mkv"}

    def __init__(
        self,
        max_workers: int = 4,
        temp_dir: str | None = None,
    ):
        """Initialize the async ASR processor.

        Args:
            max_workers: Maximum number of parallel transcription processes
            temp_dir: Directory for temporary files (default: system temp)
        """
        if not TWISTED_AVAILABLE:
            raise ImportError("Twisted is required for AsyncASRProcessor")

        if not SPEECH_RECOGNITION_AVAILABLE:
            logger.warning(
                "speech_recognition library not available. ASR will be disabled. "
                "Install with: pip install SpeechRecognition"
            )

        if not REQUESTS_AVAILABLE:
            logger.warning(
                "requests library not available. Media download will be disabled. " "Install with: pip install requests"
            )

        self.max_workers = max_workers
        self.temp_dir = temp_dir or tempfile.gettempdir()
        self.executor = ProcessPoolExecutor(max_workers=max_workers)

        logger.info(f"AsyncASRProcessor initialized with {max_workers} workers, " f"temp_dir={self.temp_dir}")

    def process_media_url(self, media_url: str, item_dict: dict[str, Any]) -> "defer.Deferred":
        """Process media URL asynchronously (download + transcribe).

        This method returns a Deferred that will fire with the transcription result.
        It does not block the Scrapy reactor.

        Args:
            media_url: URL of audio/video file to transcribe
            item_dict: Item dictionary to update with transcript

        Returns:
            Twisted Deferred that fires with updated item_dict

        Examples:
            >>> processor = AsyncASRProcessor()
            >>> deferred = processor.process_media_url(
            ...     "https://example.com/audio.mp3",
            ...     {"url": "https://example.com", "title": "Test"}
            ... )
            >>> deferred.addCallback(lambda item: print(item['transcript']))
        """
        if not REQUESTS_AVAILABLE or not SPEECH_RECOGNITION_AVAILABLE:
            # Return deferred that fires immediately with unmodified item
            return defer.succeed(item_dict)

        # Check if URL has supported format
        url_lower = media_url.lower()
        is_supported = any(
            url_lower.endswith(ext) for ext in self.SUPPORTED_AUDIO_FORMATS | self.SUPPORTED_VIDEO_FORMATS
        )

        if not is_supported:
            logger.debug(f"Unsupported media format: {media_url}")
            return defer.succeed(item_dict)

        # Download media file asynchronously
        download_deferred = threads.deferToThread(self._download_media, media_url)

        # Chain transcription after download
        download_deferred.addCallback(lambda local_path: self._transcribe_async(local_path, item_dict))

        # Handle errors gracefully
        download_deferred.addErrback(lambda failure: self._handle_error(failure, item_dict))

        return download_deferred

    def _download_media(self, media_url: str) -> str:
        """Download media file to temporary directory.

        This function may block, so it should be called via threads.deferToThread.

        Args:
            media_url: URL of media file

        Returns:
            Path to downloaded file

        Raises:
            Exception: If download fails
        """
        logger.info(f"Downloading media: {media_url}")

        # Determine file extension
        ext = Path(media_url).suffix or ".tmp"

        # Create temporary file
        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=ext,
            dir=self.temp_dir,
        )
        temp_path = temp_file.name
        temp_file.close()

        # Download file
        response = requests.get(media_url, timeout=60, stream=True)
        response.raise_for_status()

        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"Media downloaded to: {temp_path}")
        return temp_path

    def _transcribe_async(self, local_path: str, item_dict: dict[str, Any]) -> "defer.Deferred":
        """Transcribe audio file asynchronously using process pool.

        Args:
            local_path: Path to local audio file
            item_dict: Item dictionary to update

        Returns:
            Deferred that fires with updated item_dict
        """
        logger.info(f"Submitting transcription job: {local_path}")

        # Submit transcription to process pool
        future = self.executor.submit(transcribe_audio_file, local_path)

        # Convert Future to Deferred
        deferred = defer.Deferred()

        def on_complete(result_future):
            """Callback when transcription completes."""
            try:
                result = result_future.result()

                if result["success"]:
                    # Update item with transcript
                    item_dict["transcript"] = result["transcript"]
                    item_dict["media_duration"] = result.get("duration", 0)
                    logger.info(f"Transcription successful: {len(result['transcript'])} chars")
                else:
                    logger.warning(f"Transcription failed: {result['error']}")
                    item_dict["transcript"] = ""
                    item_dict["transcription_error"] = result["error"]

                # Clean up temporary file
                try:
                    os.remove(local_path)
                except Exception as e:
                    logger.warning(f"Failed to remove temp file {local_path}: {e}")

                # Fire deferred with updated item
                deferred.callback(item_dict)

            except Exception as e:
                logger.error(f"Error processing transcription result: {e}")
                deferred.errback(e)

        # Attach callback to future
        future.add_done_callback(on_complete)

        return deferred

    def _handle_error(self, failure: Any, item_dict: dict[str, Any]) -> dict[str, Any]:
        """Handle errors in download/transcription pipeline.

        Args:
            failure: Twisted Failure object
            item_dict: Original item dictionary

        Returns:
            Item dictionary with error information
        """
        logger.error(f"ASR processing failed: {failure}")
        item_dict["transcript"] = ""
        item_dict["transcription_error"] = str(failure)
        return item_dict

    def shutdown(self):
        """Shutdown the process pool executor.

        This should be called when the spider closes to ensure all processes
        are properly terminated.
        """
        logger.info("Shutting down AsyncASRProcessor")
        self.executor.shutdown(wait=True)
        logger.info("AsyncASRProcessor shutdown complete")


# Example integration with Scrapy spider
class ASRMiddleware:
    """Scrapy middleware for automatic ASR processing of media URLs.

    This middleware intercepts items with media_url fields and automatically
    submits them for asynchronous transcription. The spider continues processing
    other requests while transcription happens in the background.

    Usage:
        Add to DOWNLOADER_MIDDLEWARES or SPIDER_MIDDLEWARES in settings.py
    """

    def __init__(self, max_workers: int = 4):
        """Initialize ASR middleware.

        Args:
            max_workers: Maximum number of parallel transcription processes
        """
        self.processor = AsyncASRProcessor(max_workers=max_workers)

    @classmethod
    def from_crawler(cls, crawler):
        """Factory method to create middleware from crawler settings.

        Args:
            crawler: Scrapy crawler instance

        Returns:
            Configured ASRMiddleware instance
        """
        max_workers = crawler.settings.getint("ASR_MAX_WORKERS", 4)
        middleware = cls(max_workers=max_workers)

        # Connect to spider_closed signal to shutdown processor
        crawler.signals.connect(
            middleware.spider_closed,
            signal=crawler.signals.spider_closed,
        )

        return middleware

    def process_spider_output(self, response, result, spider):
        """Process spider output and submit media URLs for transcription.

        Args:
            response: Scrapy response object
            result: Iterable of items/requests yielded by spider
            spider: Spider instance

        Yields:
            Items/requests (potentially wrapped in Deferreds for ASR processing)
        """
        for item_or_request in result:
            # Check if this is an item with media_url
            if hasattr(item_or_request, "__getitem__"):  # Is dict-like
                media_url = item_or_request.get("media_url")

                if media_url:
                    # Submit for async transcription
                    deferred = self.processor.process_media_url(media_url, item_or_request)
                    yield deferred
                else:
                    yield item_or_request
            else:
                # This is a Request, not an Item
                yield item_or_request

    def spider_closed(self, spider):
        """Shutdown processor when spider closes.

        Args:
            spider: Spider instance
        """
        self.processor.shutdown()
