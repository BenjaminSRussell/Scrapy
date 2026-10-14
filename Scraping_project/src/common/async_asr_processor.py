import functools
import logging
import os
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from src.utils.ssrf import guarded_get

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

# Default ceiling for a single downloaded media file (#468). Long crawls hit
# many media URLs; an unbounded stream into the temp dir can fill the disk.
DEFAULT_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

try:
    from prometheus_client import Counter, Histogram

    ASR_TEMP_FILES: Any = Counter(
        "scrapy_asr_temp_files_total",
        "ASR temp media files by cleanup outcome (cleaned, leaked)",
        ["outcome"],
    )
    ASR_DOWNLOADS_REJECTED: Any = Counter(
        "scrapy_asr_downloads_rejected_total",
        "ASR media downloads aborted before transcription",
        ["reason"],
    )
    # Stage 4 only (#1101). The crawl pipeline keeps the scrapy_asr_* series above.
    ASR_JOBS: Any = Counter(
        "asr_jobs_total",
        "Stage 4 ASR transcription attempts that called the speech backend",
    )
    ASR_LATENCY: Any = Histogram(
        "asr_latency",
        "Stage 4 ASR transcription latency in seconds",
        buckets=(0.05, 0.1, 0.5, 1.0, 2.0, 5.0, 15.0, 60.0, float("inf")),
    )
    ASR_FAILURES: Any = Counter(
        "asr_failures_total",
        "Stage 4 ASR transcriptions that raised or returned an error",
    )
except Exception:  # prometheus_client missing or metric already registered
    ASR_TEMP_FILES = None
    ASR_DOWNLOADS_REJECTED = None
    ASR_JOBS = None
    ASR_LATENCY = None
    ASR_FAILURES = None


# Speech-to-text backend (#429). ``none`` (default) never downloads or sends
# media anywhere. ``google`` uploads audio to Google's Web Speech API and is an
# explicit opt-in. ``whisper`` transcribes locally (needs the openai-whisper
# package) and keeps audio on the box.
ASR_PROVIDERS = ("none", "google", "whisper")
DEFAULT_ASR_PROVIDER = "none"
_google_warning_logged = False


def resolve_asr_provider(value: str | None = None) -> str:
    """Normalize ``value`` (or ``$ASR_PROVIDER``) to one of ``ASR_PROVIDERS``.

    Unknown values fall back to ``none``. A typo must never turn into network
    egress.
    """
    raw = value if value is not None else os.environ.get("ASR_PROVIDER", DEFAULT_ASR_PROVIDER)
    provider = (raw or DEFAULT_ASR_PROVIDER).strip().lower()
    if provider not in ASR_PROVIDERS:
        logger.warning(f"Unknown ASR_PROVIDER={raw!r}; ASR disabled (choose one of {', '.join(ASR_PROVIDERS)})")
        return "none"
    return provider


def _warn_google_once() -> None:
    global _google_warning_logged
    if not _google_warning_logged:
        _google_warning_logged = True
        logger.warning(
            "ASR_PROVIDER=google: media audio will be uploaded to Google's Web Speech API "
            "for transcription. Use ASR_PROVIDER=whisper to keep audio local, or none to disable."
        )


class MediaTooLargeError(Exception):
    """The media file exceeds the configured max download size."""


def _count(metric: Any, **labels: str) -> None:
    if metric is not None:
        metric.labels(**labels).inc()


def remove_temp_file(path: str | None) -> bool:
    """Delete a temp media file; return True when it no longer exists.

    Records ``scrapy_asr_temp_files_total{outcome="cleaned"|"leaked"}`` so a
    leak (e.g. a permissions problem) shows up instead of silently filling
    the temp dir.
    """
    if not path:
        return True
    try:
        os.remove(path)
    except FileNotFoundError:
        return True
    except OSError as e:
        logger.warning(f"Failed to remove temp file {path}: {e}")
        _count(ASR_TEMP_FILES, outcome="leaked")
        return False
    _count(ASR_TEMP_FILES, outcome="cleaned")
    return True

def transcribe_audio_file(audio_path: str, language: str = "en-US", provider: str = DEFAULT_ASR_PROVIDER) -> dict[str, Any]:
    if provider not in ("google", "whisper"):
        return {
            "success": False,
            "transcript": "",
            "error": "ASR disabled (ASR_PROVIDER=none)",
            "duration": 0,
        }
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

            if provider == "google":
                # Uploads audio to Google; explicit opt-in only (#429).
                transcript = recognizer.recognize_google(audio_data, language=language)
            else:
                # Local model, no egress. Whisper takes ISO-639-1 ("en"), not "en-US".
                transcript = recognizer.recognize_whisper(audio_data, language=language.split("-")[0].lower())

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
        max_download_bytes: int | None = DEFAULT_MAX_DOWNLOAD_BYTES,
        provider: str | None = None,
    ):
        """Initialize the async ASR processor.

        Args:
            max_workers: Maximum number of parallel transcription processes
            temp_dir: Directory for temporary files (default: system temp)
            max_download_bytes: Abort (and delete) a media download larger
                than this; ``None`` or ``0`` disables the cap.
            provider: ``none`` / ``google`` / ``whisper``; defaults to
                ``$ASR_PROVIDER`` or ``none`` (no download, no egress).
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

        self.provider = resolve_asr_provider(provider)
        if self.provider == "google":
            _warn_google_once()
        elif self.provider == "none":
            logger.info("ASR provider is 'none': media URLs are passed through untranscribed")

        self.max_workers = max_workers
        self.temp_dir = temp_dir or tempfile.gettempdir()
        self.max_download_bytes = max_download_bytes or None
        self.executor = ProcessPoolExecutor(max_workers=max_workers)

        logger.info(
            f"AsyncASRProcessor initialized with {max_workers} workers, provider={self.provider}, temp_dir={self.temp_dir}"
        )

    def process_media_url(self, media_url: str, item_dict: dict[str, Any]) -> "defer.Deferred":
        if self.provider == "none" or not REQUESTS_AVAILABLE or not SPEECH_RECOGNITION_AVAILABLE:
            return defer.succeed(item_dict)

        url_lower = media_url.lower()
        is_supported = any(
            url_lower.endswith(ext) for ext in self.SUPPORTED_AUDIO_FORMATS | self.SUPPORTED_VIDEO_FORMATS
        )

        if not is_supported:
            logger.debug(f"Unsupported media format: {media_url}")
            return defer.succeed(item_dict)

        download_deferred: "defer.Deferred[Any]" = threads.deferToThread(self._download_media, media_url)

        download_deferred.addCallback(lambda local_path: self._transcribe_async(local_path, item_dict))

        download_deferred.addErrback(lambda failure: self._handle_error(failure, item_dict))

        return download_deferred

    def _download_media(self, media_url: str) -> str:
        """Stream ``media_url`` into a temp file and return its path.

        The temp file is deleted on *any* failure (HTTP error, network error
        mid-stream, size cap exceeded), so only a successful download hands a
        path to the caller, which then owns its cleanup (#468).
        """
        logger.info(f"Downloading media: {media_url}")

        ext = Path(media_url).suffix or ".tmp"
        limit = self.max_download_bytes

        temp_file = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=ext,
            dir=self.temp_dir,
        )
        temp_path = temp_file.name
        temp_file.close()

        try:
            # SSRF guard (#450): URL and every redirect hop, DNS answers included.
            response = guarded_get(media_url, session=requests, timeout=60, stream=True)
            try:
                response.raise_for_status()

                declared = response.headers.get("Content-Length")
                if limit and declared and declared.isdigit() and int(declared) > limit:
                    raise MediaTooLargeError(
                        f"{media_url} declares {declared} bytes, over the {limit}-byte limit"
                    )

                written = 0
                with open(temp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if limit and written > limit:
                            raise MediaTooLargeError(f"{media_url} exceeded the {limit}-byte limit")
                        f.write(chunk)
            finally:
                close = getattr(response, "close", None)
                if callable(close):
                    close()
        except MediaTooLargeError:
            _count(ASR_DOWNLOADS_REJECTED, reason="too_large")
            remove_temp_file(temp_path)
            raise
        except BaseException:
            _count(ASR_DOWNLOADS_REJECTED, reason="download_error")
            remove_temp_file(temp_path)
            raise

        logger.info(f"Media downloaded to: {temp_path}")
        return temp_path

    def _transcribe_async(self, local_path: str, item_dict: dict[str, Any]) -> "defer.Deferred":
        logger.info(f"Submitting transcription job: {local_path}")

        try:
            future = self.executor.submit(functools.partial(transcribe_audio_file, provider=self.provider), local_path)
        except BaseException:
            # Executor shut down / broken pool: the job never runs, so the
            # file would never be removed by on_complete.
            remove_temp_file(local_path)
            raise

        deferred: "defer.Deferred[Any]" = defer.Deferred()

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
            except BaseException as e:
                # Worker crash (BrokenProcessPool), cancellation or a bad
                # result: still remove the file before reporting the error.
                logger.error(f"Error processing transcription result: {e}")
                remove_temp_file(local_path)
                deferred.errback(e)
                return

            remove_temp_file(local_path)
            deferred.callback(item_dict)

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

class ASRPipeline:
    """Item pipeline that transcribes ``item["media_url"]`` (#470).

    Only registered when ``ASR_ENABLED`` is true (see ``src/settings.py``), so a
    default crawl never imports this module or ``speech_recognition``. Item
    pipelines may return a Deferred, which Scrapy waits on before handing the
    item to the next pipeline. Spider middleware output cannot do that, so
    this is the supported entry point. Items without ``media_url``, and
    non-dict items, pass straight through.

    Cost: one download plus one transcription process per media item, at most
    ``ASR_MAX_WORKERS`` in parallel, with downloads capped at
    ``ASR_MAX_DOWNLOAD_BYTES``. ``ASR_PROVIDER`` picks the backend (#429).
    """

    def __init__(self, processor: "AsyncASRProcessor"):
        self.processor = processor

    @classmethod
    def from_crawler(cls, crawler):
        s = crawler.settings
        return cls(
            AsyncASRProcessor(
                max_workers=s.getint("ASR_MAX_WORKERS", 4),
                temp_dir=s.get("ASR_TEMP_DIR") or None,
                max_download_bytes=s.getint("ASR_MAX_DOWNLOAD_BYTES", DEFAULT_MAX_DOWNLOAD_BYTES),
                provider=s.get("ASR_PROVIDER") or None,
            )
        )

    def process_item(self, item, spider=None):
        if not isinstance(item, dict) or not item.get("media_url"):
            return item
        return self.processor.process_media_url(item["media_url"], item)

    def close_spider(self, spider=None):
        self.processor.shutdown()


class ASRMiddleware:

    def __init__(
        self,
        max_workers: int = 4,
        temp_dir: str | None = None,
        max_download_bytes: int | None = DEFAULT_MAX_DOWNLOAD_BYTES,
        provider: str | None = None,
    ):
        self.processor = AsyncASRProcessor(
            max_workers=max_workers,
            temp_dir=temp_dir,
            max_download_bytes=max_download_bytes,
            provider=provider,
        )

    @classmethod
    def from_crawler(cls, crawler):
        max_workers = crawler.settings.getint("ASR_MAX_WORKERS", 4)
        middleware = cls(
            max_workers=max_workers,
            temp_dir=crawler.settings.get("ASR_TEMP_DIR") or None,
            max_download_bytes=crawler.settings.getint("ASR_MAX_DOWNLOAD_BYTES", DEFAULT_MAX_DOWNLOAD_BYTES),
            provider=crawler.settings.get("ASR_PROVIDER") or None,
        )

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


# Stage 4 reads local fixtures only (#1101). Remote media stays on ASRPipeline,
# which is SSRF-guarded. A missing speech library is a skip, not a failure.
_STAGE4_TEXT_SUFFIXES = {".txt", ".text", ".md"}
_STAGE4_MAX_SOURCES = 8
_STAGE4_MAX_TEXT_BYTES = 1_000_000
_missing_speech_logged = False


def _inc_metric(metric: Any) -> None:
    if metric is not None:
        metric.inc()


def _stage4_provider() -> str:
    """Local whisper when ``ASR_PROVIDER`` is unset. ``none`` and ``google`` are explicit."""
    raw = os.environ.get("ASR_PROVIDER")
    if raw is None or not str(raw).strip():
        return "whisper"
    return resolve_asr_provider(raw)


def _local_stage4_file(raw: str) -> Path | None:
    text = raw.strip()
    if not text or "://" in text:
        return None
    try:
        path = Path(text).expanduser()
        if not path.is_file():
            return None
    except OSError:
        return None
    return path


def _stage4_sources(record: dict[str, Any]) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    groups = (
        ("text", "text_path", "text_paths"),
        ("audio", "audio_path", "audio_paths"),
    )
    audio_suffixes = AsyncASRProcessor.SUPPORTED_AUDIO_FORMATS
    for kind, single, many in groups:
        values: list[str] = []
        one = record.get(single)
        if isinstance(one, str):
            values.append(one)
        group = record.get(many)
        if isinstance(group, list | tuple):
            values.extend(item for item in group if isinstance(item, str))
        for raw in values:
            path = _local_stage4_file(raw)
            if path is None:
                continue
            suffix = path.suffix.lower()
            if suffix in _STAGE4_TEXT_SUFFIXES:
                found.append(("text", path))
            elif kind == "audio" and suffix in audio_suffixes:
                found.append(("audio", path))
            if len(found) >= _STAGE4_MAX_SOURCES:
                return found
    return found


def _read_stage4_text(path: Path) -> str:
    try:
        data = path.read_bytes()[:_STAGE4_MAX_TEXT_BYTES]
    except OSError as exc:
        logger.warning("Stage 4 ASR could not read %s: %s", path, exc)
        return ""
    return data.decode("utf-8", errors="replace").strip()


def transcribe_stage4_audio(path: str, *, provider: str | None = None) -> dict[str, Any]:
    """Transcribe one local file. Missing deps and ``provider='none'`` set ``skipped``."""
    chosen = _stage4_provider() if provider is None else resolve_asr_provider(provider)
    if chosen == "none":
        return {
            "success": False,
            "transcript": "",
            "error": "ASR disabled (ASR_PROVIDER=none)",
            "skipped": True,
        }
    if chosen == "google":
        _warn_google_once()
    if not SPEECH_RECOGNITION_AVAILABLE:
        global _missing_speech_logged
        if not _missing_speech_logged:
            _missing_speech_logged = True
            logger.warning(
                "speech_recognition is not installed; Stage 4 skipped this audio file. "
                "The summary continues without a transcript."
            )
        return {
            "success": False,
            "transcript": "",
            "error": "speech_recognition library not available",
            "skipped": True,
        }
    started = time.perf_counter()
    _inc_metric(ASR_JOBS)
    try:
        result = transcribe_audio_file(path, provider=chosen)
    except Exception as exc:
        if ASR_LATENCY is not None:
            ASR_LATENCY.observe(time.perf_counter() - started)
        _inc_metric(ASR_FAILURES)
        logger.warning("Stage 4 ASR failed for %s: %s", path, exc)
        return {"success": False, "transcript": "", "error": str(exc), "skipped": False}
    if ASR_LATENCY is not None:
        ASR_LATENCY.observe(time.perf_counter() - started)
    if not result.get("success"):
        _inc_metric(ASR_FAILURES)
    return result


def stage4_asr_transcripts(record: dict[str, Any]) -> list[str]:
    """Transcripts from local fixtures. Does not check the feature flag."""
    if not isinstance(record, dict):
        return []
    provider = _stage4_provider()
    transcripts: list[str] = []
    for kind, path in _stage4_sources(record):
        if kind == "text":
            text = _read_stage4_text(path)
            if text:
                transcripts.append(text)
            continue
        result = transcribe_stage4_audio(str(path), provider=provider)
        text = (result.get("transcript") or "").strip()
        if result.get("success") and text:
            transcripts.append(text)
    return transcripts


def apply_stage4_asr(analytics: dict[str, Any]) -> dict[str, Any]:
    """Fold fixture transcripts into a copy of ``analytics``. Flag off returns it unchanged."""
    from src.utils.feature_flags import get_bool

    if not isinstance(analytics, dict) or not get_bool("STAGE4_ASR_ENABLED", False):
        return analytics
    added = stage4_asr_transcripts(analytics)
    if not added:
        return analytics
    data = dict(analytics)
    existing = data.get("audio_transcripts") or []
    if not isinstance(existing, list):
        existing = []
    data["audio_transcripts"] = [*existing, *added]
    combined = data.get("combined_text") or ""
    if not isinstance(combined, str):
        combined = ""
    extra = "\n".join(added)
    data["combined_text"] = f"{combined}\n{extra}".strip() if combined.strip() else extra
    return data
