"""Helpers for optional ML/OCR extras (#144).

Core install (requirements.txt) supports Stage 1 discovery, Stage 2 analysis,
and light Stage 3 (datasketch MinHash dedupe + extractive summary) without
torch. Stage 4 and ml_service require ML and/or OCR extras via
requirements-ml.txt / requirements-ocr.txt.

Packaging extras (.[ml]/.[ocr]/.[stage3-4]) are defined in PR #289 — prefer
the requirements-*.txt install paths from this PR until that lands.
"""

from __future__ import annotations

from typing import Iterable

ML_INSTALL_HINT = (
    "Install ML extras with:\n"
    "  pip install -r requirements-ml.txt\n"
    "  # (or, once PR #289 packaging lands: pip install -e '.[ml]')\n"
    "Docker: build/run target `ml` or `stage4` "
    "(compose profile `ml` or `full`)."
)

OCR_INSTALL_HINT = (
    "Install OCR extras with:\n"
    "  pip install -r requirements-ocr.txt\n"
    "  # (or, once PR #289 packaging lands: pip install -e '.[ocr]')\n"
    "Also install system poppler-utils for pdf2image. "
    "Docker target `ml`/`stage4` includes OCR extras."
)

STAGE3_INSTALL_HINT = (
    "Stage 3 (light) needs datasketch, which is part of the core install:\n"
    "  pip install -r requirements.txt\n"
    "Docker: build/run target `core` or `stage3` (no ML extras required)."
)

# (import_name, pip_package_name)
_ML_MODULES: tuple[tuple[str, str], ...] = (
    ("torch", "torch"),
    ("transformers", "transformers"),
    ("sentence_transformers", "sentence-transformers"),
    ("sklearn", "scikit-learn"),
)

_OCR_MODULES: tuple[tuple[str, str], ...] = (
    ("easyocr", "easyocr"),
    ("pdf2image", "pdf2image"),
    ("PyPDF2", "PyPDF2"),
    ("PIL", "pillow"),
)

# Light Stage 3 path: MinHash dedupe only (already in requirements.in / core lock)
_STAGE3_CORE_MODULES: tuple[tuple[str, str], ...] = (
    ("datasketch", "datasketch"),
)


def _missing(modules: Iterable[tuple[str, str]]) -> list[str]:
    missing: list[str] = []
    for import_name, pip_name in modules:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(pip_name)
    return missing


def missing_ml_packages() -> list[str]:
    """Return pip names of missing ML packages."""
    return _missing(_ML_MODULES)


def missing_ocr_packages() -> list[str]:
    """Return pip names of missing OCR packages."""
    return _missing(_OCR_MODULES)


def missing_stage3_packages() -> list[str]:
    """Return pip names missing for light Stage 3 (datasketch / core)."""
    return _missing(_STAGE3_CORE_MODULES)


def require_ml_deps(context: str = "This component") -> None:
    """Fail fast with a clear install hint if ML extras are absent.

    Raises:
        ImportError: when one or more ML packages are not installed.
    """
    missing = missing_ml_packages()
    if missing:
        raise ImportError(
            f"{context} requires ML extras; missing: {', '.join(missing)}.\n"
            f"{ML_INSTALL_HINT}"
        )


def require_ocr_deps(context: str = "This component") -> None:
    """Fail fast with a clear install hint if OCR extras are absent."""
    missing = missing_ocr_packages()
    if missing:
        raise ImportError(
            f"{context} requires OCR extras; missing: {', '.join(missing)}.\n"
            f"{OCR_INSTALL_HINT}"
        )


def require_stage3_deps() -> None:
    """Light Stage 3 needs datasketch only (core lock) — not torch/ML extras.

    The current Stage 3 worker uses MinHashLSH dedupe + extractive sentence
    truncation; it does not import torch/transformers. Keep fail-fast aligned
    with those imports so a core image can run Stage 3.
    """
    missing = missing_stage3_packages()
    if missing:
        raise ImportError(
            f"Stage 3 requires core deps; missing: {', '.join(missing)}.\n"
            f"{STAGE3_INSTALL_HINT}"
        )


def require_stage4_deps() -> None:
    """Stage 4 workers need ML; OCR is required for PDF/image document paths."""
    require_ml_deps("Stage 4")
    # OCR is strongly recommended for Stage 4 large docs; fail fast so lean
    # images do not silently skip PDF/OCR paths.
    require_ocr_deps("Stage 4")
