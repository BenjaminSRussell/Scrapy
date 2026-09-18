"""Helpers for optional ML/OCR extras (#144).

Core install (requirements.txt) supports Stage 1 discovery and Stage 2 analysis
without torch. Stage 3/4 and ml_service require ML and/or OCR extras via
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
    "Docker: build/run target `ml`, `stage3`, or `stage4` "
    "(compose profile `ml` or `full`)."
)

OCR_INSTALL_HINT = (
    "Install OCR extras with:\n"
    "  pip install -r requirements-ocr.txt\n"
    "  # (or, once PR #289 packaging lands: pip install -e '.[ocr]')\n"
    "Also install system poppler-utils for pdf2image. "
    "Docker target `ml`/`stage4` includes OCR extras."
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
    """Stage 3 workers need the ML extra (transformers/torch stack)."""
    require_ml_deps("Stage 3")


def require_stage4_deps() -> None:
    """Stage 4 workers need ML; OCR is required for PDF/image document paths."""
    require_ml_deps("Stage 4")
    # OCR is strongly recommended for Stage 4 large docs; fail fast so lean
    # images do not silently skip PDF/OCR paths.
    require_ocr_deps("Stage 4")
