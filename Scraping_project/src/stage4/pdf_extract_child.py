"""Child process for sandboxed PDF text extraction (#445).

Reads PDF bytes on stdin and writes UTF-8 text to stdout. It runs in its own
process so a pathological PDF can only kill this child, never the Stage 4
worker. Exit codes: 0 ok, 2 parse error, 3 out of memory (RLIMIT_AS hit),
4 no PDF library.
"""

from __future__ import annotations

import os
import sys

EXIT_OK, EXIT_ERROR, EXIT_OOM, EXIT_NO_LIB = 0, 2, 3, 4


def apply_limits() -> None:
    """Cap this process's address space at ``STAGE4_PDF_MAX_RSS_MB``.

    Python raises MemoryError at the cap (mapped to exit 3) instead of the
    kernel/cgroup OOM killer taking out the parent worker.
    """
    mb = int(os.getenv("STAGE4_PDF_MAX_RSS_MB", "0") or 0)
    if mb <= 0:
        return
    try:
        import resource

        limit = mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except (ImportError, ValueError, OSError):  # pragma: no cover - non-POSIX
        pass


def _reader_cls():
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader  # requirements pin PyPDF2 3.x (same API)
    return PdfReader


def main() -> int:
    apply_limits()
    try:
        from io import BytesIO

        try:
            reader_cls = _reader_cls()
        except ImportError:
            sys.stderr.write("no PDF library (pypdf/PyPDF2) installed")
            return EXIT_NO_LIB
        data = sys.stdin.buffer.read()
        max_pages = int(os.getenv("STAGE4_PDF_MAX_PAGES", "2000") or 2000)
        reader = reader_cls(BytesIO(data))
        parts: list[str] = []
        for i, page in enumerate(reader.pages):
            if i >= max_pages:
                break
            parts.append(page.extract_text() or "")
        sys.stdout.buffer.write(" ".join(parts).encode("utf-8", errors="replace"))
        sys.stdout.flush()
        return EXIT_OK
    except MemoryError:
        os._exit(EXIT_OOM)  # skip cleanup: the heap is exhausted
    except Exception as e:
        sys.stderr.write(f"{type(e).__name__}: {e}"[:500])
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
