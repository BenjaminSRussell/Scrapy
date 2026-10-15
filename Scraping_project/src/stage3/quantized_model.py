"""CPU-only quantized Stage 3 option (#652).

``stage3.model_variant: quantized`` selects an int8/gguf distill path. The
runtime here is an offline stub: it does not import torch, does not download
weights, and does not read ``requirements-stage4.txt``. The default variant
``full`` keeps the worker's existing extractive summary.

The pinned full-precision checkpoint stays ``sshleifer/distilbart-cnn-12-6``
in ``models.lock.json``. This module does not add that file, or a GGUF, to
the lean stage3 image.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

VARIANT_FULL = "full"
VARIANT_QUANTIZED = "quantized"

# Local name only. Not a Hugging Face repo id and not a models.lock.json pin.
# A later change can point this at a real int8 GGUF without pulling torch into
# the stage3 image.
QUANTIZED_MODEL_ID = "local-stub://stage3/distilbart-cnn-12-6-q8_0.gguf"
QUANTIZED_FORMAT = "int8-gguf"
# Shorter than SUMMARY_LIMITS["extractive_max_sentences"] (5) so the stub is
# visibly lossier than the default extractive cut.
QUANTIZED_MAX_SENTENCES = 2

QUALITY_CAVEAT = (
    "Quality caveat: stage3.model_variant quantized is an int8/gguf distill stub, "
    "not the pinned full-precision sshleifer/distilbart-cnn-12-6 checkpoint. "
    "The offline stub keeps at most two sentences and can drop entities, numbers, "
    "and later context. It is not citation-grade. The default variant full keeps "
    "the existing five-sentence extractive summary and also does not load torch."
)

IMAGE_SIZE_NOTE = (
    "Image size: the stage3 image installs requirements.txt only and does not "
    "install torch or requirements-stage4.txt. models.lock.json pins "
    "pytorch_model.bin for sshleifer/distilbart-cnn-12-6 at 1222317369 bytes; "
    "that file is not copied into the stage3 image. This quantized option adds "
    "no GGUF blob either. A real int8 GGUF of that distill checkpoint is much "
    "smaller than the 1.2 GB fp32 bin and would still stay off the lean image "
    "until a later pin. No image was rebuilt for this note."
)


def normalize_variant(raw: object) -> str:
    """Return ``full`` or ``quantized``. Anything else warns and stays ``full``.

    Only the string ``quantized`` opts in. ``True``, ``1``, and ``"yes"`` do not.
    """
    if raw is None:
        return VARIANT_FULL
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in ("", VARIANT_FULL):
            return VARIANT_FULL
        if value == VARIANT_QUANTIZED:
            return VARIANT_QUANTIZED
    logger.warning("stage3.model_variant=%r is not 'full' or 'quantized'; using full", raw)
    return VARIANT_FULL


def configured_variant() -> str:
    """``stage3.model_variant`` from config.yml, default ``full``."""
    from src.core.config import get_config

    return normalize_variant(get_config().get("stage3.model_variant", VARIANT_FULL))


def log_selected_variant(variant: str) -> None:
    """Log the quantized choice once. ``full`` is silent."""
    if variant != VARIANT_QUANTIZED:
        return
    logger.info(
        "Stage 3 model_variant=%s model=%s format=%s. %s %s",
        VARIANT_QUANTIZED,
        QUANTIZED_MODEL_ID,
        QUANTIZED_FORMAT,
        QUALITY_CAVEAT,
        IMAGE_SIZE_NOTE,
    )


def quantized_offline_summary(text: str, *, max_chars: int) -> str:
    """Offline int8/gguf stand-in. No torch, no socket, no weight file."""
    sentences = str(text).split(".")[:QUANTIZED_MAX_SENTENCES]
    body = ". ".join(sentence.strip() for sentence in sentences if sentence.strip())
    summary = body + "." if body else ""
    if len(summary) > max_chars:
        from src.utils.text_truncate import truncate_text

        return truncate_text(summary, max_chars)
    return summary


def run_quantized_smoke(text: str, *, max_chars: int = 1000) -> dict[str, Any]:
    """Run the quantized path as an offline stub.

    Used by the unit smoke test. Does not load a model and does not change
    the default ``full`` worker.
    """
    summary = quantized_offline_summary(text, max_chars=max_chars)
    return {
        "variant": VARIANT_QUANTIZED,
        "model_id": QUANTIZED_MODEL_ID,
        "format": QUANTIZED_FORMAT,
        "summary": summary,
        "quality_caveat": QUALITY_CAVEAT,
        "image_size_note": IMAGE_SIZE_NOTE,
    }
