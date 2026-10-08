import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.core.constants import DATA_DIR, SUMMARY_LIMITS

logger = logging.getLogger(__name__)

def validate_summary_lengths(min_length: Any, max_length: Any) -> tuple[int, int]:
    """Check generation bounds: ``0 <= min_length <= max_length`` and ``max_length >= 1`` (#232).

    Raises ``ValueError`` naming the bad value. A config with min > max used to
    reach the model and fail inside generate(), silently degrading to the
    500-char fallback for every document.
    """
    for name, value in (("min_length", min_length), ("max_length", max_length)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{name} must be an integer, got {value!r}")
    if max_length < 1:
        raise ValueError(f"max_length must be >= 1, got {max_length}")
    if min_length < 0:
        raise ValueError(f"min_length must be >= 0, got {min_length}")
    if min_length > max_length:
        raise ValueError(f"min_length ({min_length}) must be <= max_length ({max_length})")
    return min_length, max_length


def _fallback(text: str) -> str:
    return text[:500] + "..." if len(text) > 500 else text


def _truncate_at_word(text: str, limit: int) -> str:
    """At most ``limit`` chars, cut at the last whitespace so no word is split."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    head = cut.rsplit(None, 1)[0] if any(ch.isspace() for ch in cut) else cut
    return head or cut


def summarize_with_heavy_model(
    text: str,
    *,
    min_length: int | None = None,
    max_length: int | None = None,
    summarizer: Callable[..., Any] | None = None,
) -> str:
    """Abstractive summary of ``text`` (BART by default).

    * ``min_length``/``max_length`` default to ``SUMMARY_LIMITS`` and are
      validated (``ValueError`` on an impossible pair).
    * Empty/whitespace input returns ``""``; non-string input raises ``TypeError``.
    * Input with no more words than ``min_length`` is returned as-is: the
      model would have to pad it with invented text to reach ``min_length``.
    * Input is truncated to ``SUMMARY_LIMITS["chunk_size"]`` chars at a word
      boundary before it reaches the model.
    * Model missing or failing: the first 500 chars (+ "...") are returned.
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    min_length, max_length = validate_summary_lengths(
        SUMMARY_LIMITS["min_length"] if min_length is None else min_length,
        SUMMARY_LIMITS["max_length"] if max_length is None else max_length,
    )
    text = text.strip()
    if not text:
        return ""
    if len(text.split()) <= min_length:
        return text

    try:
        if summarizer is None:
            from transformers import pipeline

            summarizer = pipeline(
                "summarization",
                model="facebook/bart-large-cnn",
                device=-1,
            )

        text = _truncate_at_word(text, SUMMARY_LIMITS["chunk_size"])

        summary = summarizer(
            text,
            max_length=max_length,
            min_length=min_length,
            do_sample=False,
        )

        return str(summary[0]["summary_text"])

    except ImportError:
        logger.warning("Transformers not installed for summarization")
        return _fallback(text)
    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        return _fallback(text)

def extract_key_facts(text: str, summary: str, categories: list[str]) -> list[str]:
    sentences = text.split(".")
    key_facts = []

    for sentence in sentences[:20]:
        sentence = sentence.strip()
        if not sentence:
            continue

        for category in categories:
            if category.lower() in sentence.lower():
                key_facts.append(sentence)
                break

        if len(key_facts) >= 5:
            break

    if not key_facts:
        key_facts = [s.strip() for s in sentences[:3] if s.strip()]

    return key_facts

def create_final_summary(analytics_data: dict) -> dict:
    url = analytics_data.get("url")
    combined_text = analytics_data.get("combined_text", "")
    metadata = analytics_data.get("metadata", {})
    categories = analytics_data.get("initial_categories", [])

    if not combined_text:
        logger.warning(f"No text to summarize for {url}")
        return {
            "url": url,
            "title": metadata.get("title", "Unknown"),
            "summary": "No content available",
            "key_facts": [],
            "categories": categories,
            "type": metadata.get("type", "unknown"),
        }

    logger.info(f"Summarizing {url}...")
    summary = summarize_with_heavy_model(combined_text)

    key_facts = extract_key_facts(combined_text, summary, categories)

    final = {
        "url": url,
        "title": analytics_data.get("html_title") or metadata.get("title", "Unknown"),
        "summary": summary,
        "key_facts": key_facts,
        "categories": categories,
        "type": metadata.get("type", "webpage"),
        "has_ocr": len(analytics_data.get("ocr_texts", [])) > 0,
        "has_audio": len(analytics_data.get("audio_transcripts", [])) > 0,
        "has_video": len(analytics_data.get("video_transcripts", [])) > 0,
        "word_count": len(combined_text.split()),
        "source_metadata": metadata,
    }

    logger.info(f" Created summary for {url}")

    return final

def save_to_jsonl(summaries: list[dict], output_file: Path | None = None):
    if output_file is None:
        output_file = DATA_DIR / "final_summaries.jsonl"

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "a", encoding="utf-8") as f:
        for summary in summaries:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    logger.info(f" Saved {len(summaries)} summaries to {output_file}")

    return output_file
