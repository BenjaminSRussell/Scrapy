"""Optional Playwright screenshots for the javascript spider (#630).

Off unless ``JS_SCREENSHOTS_ENABLED`` is set (or ``feature_flags.js_screenshots_enabled``).
Each capture writes ``data/screenshots/{url_hash}.png`` (sha256 of the canonical
URL, 16 hex chars). The directory is gitignored.

Retention runs after a successful capture: PNGs older than
``stage1.js_screenshot_retention_days`` (env ``JS_SCREENSHOT_RETENTION_DAYS``,
default 7; ``0`` keeps every file) are deleted, then only the newest
``stage1.js_screenshot_max_files`` (env ``JS_SCREENSHOT_MAX_FILES``, default 200;
``0`` means no count cap) are kept. A capture error never fails the crawl.
"""

from __future__ import annotations

import inspect
import logging
import os
import time
from pathlib import Path
from typing import Any

from src.core.constants import PROJECT_ROOT
from src.utils.feature_flags import get_bool
from src.utils.url_canon import url_hash

logger = logging.getLogger(__name__)

FLAG = "JS_SCREENSHOTS_ENABLED"
RETENTION_ENV = "JS_SCREENSHOT_RETENTION_DAYS"
MAX_FILES_ENV = "JS_SCREENSHOT_MAX_FILES"
RETENTION_CONFIG = "stage1.js_screenshot_retention_days"
MAX_FILES_CONFIG = "stage1.js_screenshot_max_files"
DEFAULT_RETENTION_DAYS = 7
DEFAULT_MAX_FILES = 200
SCREENSHOT_TIMEOUT_MS = 5000
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def screenshots_enabled() -> bool:
    """True only when the operator opted in. Experimental default is off."""
    return get_bool(FLAG, False)


def screenshot_directory(base: Path | None = None) -> Path:
    """``Scraping_project/data/screenshots`` (or ``base / data/screenshots``)."""
    root = PROJECT_ROOT if base is None else Path(base)
    return root / "data" / "screenshots"


def _non_negative_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if number < 0:
        return default
    return number


def _resolve_int(env_name: str, config_key: str, default: int) -> int:
    raw = os.environ.get(env_name)
    if raw is not None and raw.strip() != "":
        return _non_negative_int(raw, default)
    try:
        from src.core.config import get_config

        value = get_config().get(config_key, default)
    except Exception:
        return default
    if value is None:
        return default
    return _non_negative_int(value, default)


def retention_days() -> int:
    return _resolve_int(RETENTION_ENV, RETENTION_CONFIG, DEFAULT_RETENTION_DAYS)


def max_files() -> int:
    return _resolve_int(MAX_FILES_ENV, MAX_FILES_CONFIG, DEFAULT_MAX_FILES)


def _is_png(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(8) == _PNG_MAGIC
    except OSError:
        return False


async def _invoke_screenshot(shot: Any, dest: Path) -> None:
    """Call Playwright's async (or sync) ``page.screenshot``. Never decodes the image.

    Extra options are dropped when the callable only accepts ``path``. The
    ``TypeError`` from an async def is raised on await, not on the call.
    """
    try:
        result = shot(path=str(dest), type="png", full_page=True, timeout=SCREENSHOT_TIMEOUT_MS)
        if inspect.isawaitable(result):
            await result
        return
    except TypeError:
        result = shot(path=str(dest))
    if inspect.isawaitable(result):
        await result


def prune_screenshots(
    directory: Path,
    *,
    retention_days: int,
    max_files: int,
    now: float | None = None,
    keep: Path | None = None,
) -> int:
    """Delete expired and over-cap ``*.png`` files. ``keep`` is never removed.

    ``retention_days <= 0`` skips the age rule. ``max_files <= 0`` skips the cap.
    Returns how many files were removed. Does not raise.
    """
    if not directory.is_dir():
        return 0
    now_s = time.time() if now is None else now
    cutoff = None if retention_days <= 0 else now_s - (retention_days * 86400)
    keep_resolved: Path | None
    try:
        keep_resolved = keep.resolve() if keep is not None else None
    except OSError:
        keep_resolved = None

    removed = 0
    protected: list[Path] = []
    survivors: list[tuple[float, Path]] = []
    for png in directory.glob("*.png"):
        if not png.is_file():
            continue
        try:
            mtime = png.stat().st_mtime
            resolved = png.resolve()
        except OSError:
            continue
        if keep_resolved is not None and resolved == keep_resolved:
            protected.append(png)
            continue
        if cutoff is not None and mtime < cutoff:
            if _unlink(png):
                removed += 1
            continue
        survivors.append((mtime, png))

    if max_files > 0:
        room = max_files - len(protected)
        if room < 0:
            room = 0
        if len(survivors) > room:
            survivors.sort(key=lambda item: item[0], reverse=True)
            for _, png in survivors[room:]:
                if _unlink(png):
                    removed += 1

    # Abandoned capture temps (crash between write and replace). Not screenshots.
    part_cutoff = now_s - 86400
    for part in directory.glob("*.part"):
        try:
            if part.is_file() and part.stat().st_mtime < part_cutoff:
                if _unlink(part):
                    removed += 1
        except OSError:
            continue
    return removed


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
    except OSError:
        return False
    return True


async def capture_page_screenshot(
    page: Any,
    url: str,
    *,
    directory: Path | None = None,
    enabled: bool | None = None,
    retention_days: int | None = None,
    max_files: int | None = None,
) -> Path | None:
    """Save a PNG when the flag is on and the page can still be shot.

    Returns the PNG path, or None when disabled, impossible, or the write failed.
    Never raises.
    """
    if enabled is None:
        enabled = screenshots_enabled()
    if not enabled or page is None or not isinstance(url, str) or not url.strip():
        return None
    try:
        is_closed = getattr(page, "is_closed", None)
        closed = bool(callable(is_closed) and is_closed())
    except Exception:
        closed = False
    if closed:
        logger.debug("[JAVASCRIPT] screenshot skipped; page already closed url=%s", url[:80])
        return None
    shot = getattr(page, "screenshot", None)
    if not callable(shot):
        logger.debug("[JAVASCRIPT] screenshot skipped; page has no screenshot() url=%s", url[:80])
        return None

    dest_dir = Path(directory) if directory is not None else screenshot_directory()
    dest = dest_dir / f"{url_hash(url)}.png"
    if retention_days is None:
        days = _resolve_int(RETENTION_ENV, RETENTION_CONFIG, DEFAULT_RETENTION_DAYS)
    else:
        days = retention_days
    if max_files is None:
        cap = _resolve_int(MAX_FILES_ENV, MAX_FILES_CONFIG, DEFAULT_MAX_FILES)
    else:
        cap = max_files
    part = dest_dir / f"{dest.stem}.{os.getpid()}.{time.time_ns()}.part"
    written: Path | None = None
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        await _invoke_screenshot(shot, part)
        if not _is_png(part):
            logger.warning("[JAVASCRIPT] screenshot for %s was not a PNG; discarded", url_hash(url))
        else:
            os.replace(part, dest)
            written = dest
    except Exception as exc:
        logger.warning("[JAVASCRIPT] screenshot failed for %s: %s", url[:80], exc)
        written = None
    finally:
        try:
            if part.is_file():
                part.unlink()
        except OSError:
            pass
    if written is None:
        return None
    try:
        removed = prune_screenshots(dest_dir, retention_days=days, max_files=cap, keep=written)
    except Exception as exc:
        logger.warning("[JAVASCRIPT] screenshot retention failed: %s", exc)
        removed = 0
    if removed:
        logger.info("[JAVASCRIPT] screenshot retention removed %d file(s)", removed)
    logger.info("[JAVASCRIPT] screenshot %s", written.name)
    return written
