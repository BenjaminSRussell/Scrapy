"""Run PDF extraction under explicit memory/time/size budgets (#445).

``extract_pdf_text`` returns the text or raises ``PdfQuarantined(reason)``
with one of: ``too_large``, ``oom``, ``timeout``, ``parse_error`` (a missing PDF
library raises RuntimeError instead: transient, the row stays pending). Callers mark the queue row ``quarantined:<reason>``, so
an OOM/timeout never leaves the row pending (re-killing the worker forever).
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from src.stage4.pdf_extract_child import EXIT_NO_LIB, EXIT_OK, EXIT_OOM

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARGV = (sys.executable, "-m", "src.stage4.pdf_extract_child")

try:  # pragma: no cover - metrics optional
    from prometheus_client import Counter

    STAGE4_OCR_OOM: Optional[Counter] = Counter(
        "stage4_ocr_oom_total", "PDF extractions killed by the memory budget (#445)."
    )
    STAGE4_OCR_TIMEOUT: Optional[Counter] = Counter(
        "stage4_ocr_timeout_total", "PDF extractions killed by the time budget (#445)."
    )
    STAGE4_PDF_QUARANTINED: Optional[Counter] = Counter(
        "stage4_pdf_quarantined_total", "PDFs quarantined instead of summarized, by reason (#445).", ["reason"]
    )
except Exception:  # pragma: no cover
    STAGE4_OCR_OOM = STAGE4_OCR_TIMEOUT = STAGE4_PDF_QUARANTINED = None

# Signals that mean "killed for memory" (cgroup OOM killer / RLIMIT abort / segfault in decoder).
_OOM_SIGNALS = {signal.SIGKILL, signal.SIGABRT, signal.SIGSEGV}


class PdfQuarantined(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _quarantine(reason: str, detail: str = "") -> PdfQuarantined:
    if reason == "oom" and STAGE4_OCR_OOM is not None:
        STAGE4_OCR_OOM.inc()
    if reason == "timeout" and STAGE4_OCR_TIMEOUT is not None:
        STAGE4_OCR_TIMEOUT.inc()
    if STAGE4_PDF_QUARANTINED is not None:
        STAGE4_PDF_QUARANTINED.labels(reason=reason).inc()
    logger.warning(f"[STAGE4] PDF quarantined ({reason}) {detail}".rstrip())
    return PdfQuarantined(reason, detail)


def extract_pdf_text(
    pdf_bytes: bytes,
    *,
    max_bytes: Optional[int] = None,
    max_rss_mb: Optional[int] = None,
    timeout_s: Optional[float] = None,
    argv: Optional[Sequence[str]] = None,
) -> str:
    max_bytes = max_bytes if max_bytes is not None else _env_int("STAGE4_PDF_MAX_BYTES", 50 * 1024 * 1024)
    max_rss_mb = max_rss_mb if max_rss_mb is not None else _env_int("STAGE4_PDF_MAX_RSS_MB", 1024)
    timeout_s = timeout_s if timeout_s is not None else float(_env_int("STAGE4_PDF_TIMEOUT_S", 120))

    if max_bytes > 0 and len(pdf_bytes) > max_bytes:
        raise _quarantine("too_large", f"{len(pdf_bytes)} bytes > {max_bytes}")

    env = dict(os.environ)
    env["STAGE4_PDF_MAX_RSS_MB"] = str(max_rss_mb)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(PROJECT_ROOT), env.get("PYTHONPATH", "")]))
    try:
        proc = subprocess.run(
            list(argv or DEFAULT_ARGV),
            input=pdf_bytes,
            capture_output=True,
            timeout=timeout_s,
            cwd=str(PROJECT_ROOT),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:  # child is killed by subprocess.run
        raise _quarantine("timeout", f"> {timeout_s:.0f}s") from None

    rc = proc.returncode
    stderr = proc.stderr.decode("utf-8", errors="replace")[-300:]
    if rc == EXIT_OK:
        return proc.stdout.decode("utf-8", errors="replace")
    if rc == EXIT_OOM or (rc < 0 and -rc in _OOM_SIGNALS):
        raise _quarantine("oom", f"rc={rc} budget={max_rss_mb}MB")
    if rc == EXIT_NO_LIB:
        # Deployment problem, not the document's fault: don't quarantine every
        # PDF; fail transiently so rows stay pending until the image is fixed.
        logger.error(f"[STAGE4] PDF extraction unavailable: {stderr}")
        raise RuntimeError(f"no PDF library in the Stage 4 image: {stderr}")
    raise _quarantine("parse_error", f"rc={rc} {stderr}")
