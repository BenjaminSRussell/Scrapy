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
            audio_data = recognizer.record(source)

            duration = len(audio_data.frame_data) / audio_data.sample_rate

            # Note: This requires internet connection
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
                "requests library not available. Media download will be disabled. Install with: pip install requests"
            )

        self.max_workers = max_workers
        self.temp_dir = temp_dir or tempfile.gettempdir()
        self.executor = ProcessPoolExecutor(max_workers=max_workers)

        logger.info(f"AsyncASRProcessor initialized with {max_workers} workers, temp_dir={self.temp_dir}")

    def process_media_url(self, media_url: str, item_dict: dict[str, Any]) -> "defer.Deferred":
        if not REQUESTS_AVAILABLE or not SPEECH_RECOGNITION_AVAILABLE:
            return defer.succeed(item_dict)

        url_lower = media_url.lower()
        is_supported = any(
            url_lower.endswith(ext) for ext in self.SUPPORTED_AUDIO_FORMATS | self.SUPPORTED_VIDEO_FORMATS
        )

        if not is_supported:
            logger.debug(f"Unsupported media format: {media_url}")
            return defer.succeed(item_dict)

        download_deferred = threads.deferToThread(self._download_media, media_url)

        download_deferred.addCallback(lambda local_path: self._transcribe_async(local_path, item_dict))

        download_deferred.addErrback(lambda failure: self._handle_error(failure, item_dict))

        return download_deferred

    def _download_media(self, media_url: str) -> str:
        logger.info(f"Downloading media: {media_url}")

        ext = Path(media_url).suffix or ".tmp"

        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=ext,
            dir=self.temp_dir,
        )
        temp_path = temp_file.name
        temp_file.close()

        response = requests.get(media_url, timeout=60, stream=True)
        response.raise_for_status()

        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logger.info(f"Media downloaded to: {temp_path}")
        return temp_path

    def _transcribe_async(self, local_path: str, item_dict: dict[str, Any]) -> "defer.Deferred":
        logger.info(f"Submitting transcription job: {local_path}")

        future = self.executor.submit(transcribe_audio_file, local_path)

        deferred = defer.Deferred()

        def on_complete(result_future):
            try:
                result = result_future.result()

                if result["success"]:
                    item_dict["transcript"] = result["transcript"]
                    item_dict["media_duration"] = result.get("duration", 0)
                    logger.info(f"Transcription successful: {len(result['transcript'])} chars")
                else:
                    logger.warning(f"Transcription failed: {result['error']}")
                    item_dict["transcript"] = ""
                    item_dict["transcription_error"] = result["error"]

                try:
                    os.remove(local_path)
                except Exception as e:
                    logger.warning(f"Failed to remove temp file {local_path}: {e}")

                deferred.callback(item_dict)

            except Exception as e:
                logger.error(f"Error processing transcription result: {e}")
                deferred.errback(e)

        future.add_done_callback(on_complete)

        return deferred

    def _handle_error(self, failure: Any, item_dict: dict[str, Any]) -> dict[str, Any]:
        logger.error(f"ASR processing failed: {failure}")
        item_dict["transcript"] = ""
        item_dict["transcription_error"] = str(failure)
        return item_dict

    def shutdown(self):
        logger.info("Shutting down AsyncASRProcessor")
        self.executor.shutdown(wait=True)
        logger.info("AsyncASRProcessor shutdown complete")

class ASRMiddleware:

    def __init__(self, max_workers: int = 4):
        self.processor = AsyncASRProcessor(max_workers=max_workers)

    @classmethod
    def from_crawler(cls, crawler):
        max_workers = crawler.settings.getint("ASR_MAX_WORKERS", 4)
        middleware = cls(max_workers=max_workers)

        crawler.signals.connect(
            middleware.spider_closed,
            signal=crawler.signals.spider_closed,
        )

        return middleware

    def process_spider_output(self, response, result, spider):
        for item_or_request in result:
            if hasattr(item_or_request, "__getitem__"):
                media_url = item_or_request.get("media_url")

                if media_url:
                    deferred = self.processor.process_media_url(media_url, item_or_request)
                    yield deferred
                else:
                    yield item_or_request
            else:
                yield item_or_request

    def spider_closed(self, spider):
        self.processor.shutdown()
