"""Stage 4: Abstractive Summarization

This stage uses a heavy transformer model (e.g., BART) to generate
abstractive summaries from the text content processed in previous stages.
Abstractive summarization means the model generates new sentences to capture
the meaning of the source text, rather than simply extracting existing sentences.

The process is computationally intensive and is designed to produce high-quality,
concise summaries for the most important documents.
"""

import json
import logging
from pathlib import Path

from src.common.constants import DATA_DIR, SUMMARY_LIMITS

logger = logging.getLogger(__name__)


def summarize_with_heavy_model(text: str) -> str:
    """Use a heavy model (BART) to create an abstractive summary of the text.

    Args:
        text: The input text to summarize.

    Returns:
        A concise, abstractive summary paragraph, or a fallback summary if
        an error occurs.
    """
    try:
        from transformers import pipeline

        # Use BART for summarization (heavy but good)
        summarizer = pipeline(
            "summarization",
            model="facebook/bart-large-cnn",
            device=-1,  # CPU
        )

        # Limit input text to the model's max input size
        max_input = SUMMARY_LIMITS["chunk_size"]
        if len(text) > max_input:
            text = text[:max_input]

        summary = summarizer(
            text,
            max_length=SUMMARY_LIMITS["max_length"],
            min_length=SUMMARY_LIMITS["min_length"],
            do_sample=False,
        )

        return summary[0]["summary_text"]

    except ImportError:
        logger.warning("Transformers not installed for summarization")
        # Fallback: first N characters
        return text[:500] + "..." if len(text) > 500 else text
    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        return text[:500] + "..." if len(text) > 500 else text


def extract_key_facts(text: str, summary: str, categories: list[str]) -> list[str]:
    """Extract key facts from the text.

    Uses the summary and categories to identify the most important information.
    """
    # Simple approach: extract sentences with category keywords
    sentences = text.split(".")
    key_facts = []

    # Get sentences that contain category keywords
    for sentence in sentences[:20]:  # First 20 sentences
        sentence = sentence.strip()
        if not sentence:
            continue

        # Check if sentence contains any category keyword
        for category in categories:
            if category.lower() in sentence.lower():
                key_facts.append(sentence)
                break

        if len(key_facts) >= 5:  # Max 5 key facts
            break

    # If no facts found, use first few sentences
    if not key_facts:
        key_facts = [s.strip() for s in sentences[:3] if s.strip()]

    return key_facts


def create_final_summary(analytics_data: dict) -> dict:
    """Create final summary from stage 2 analytics data.

    Returns:
        Summary dict ready for JSONL output and Delta Lake

    """
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

    # Generate summary
    logger.info(f"Summarizing {url}...")
    summary = summarize_with_heavy_model(combined_text)

    # Extract key facts
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

    logger.info(f"✅ Created summary for {url}")

    return final


def save_to_jsonl(summaries: list[dict], output_file: Path | None = None):
    """Save final summaries to JSONL file.

    One line per URL with summary paragraph and key facts.
    """
    if output_file is None:
        output_file = DATA_DIR / "final_summaries.jsonl"

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, "a", encoding="utf-8") as f:
        for summary in summaries:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")

    logger.info(f"✅ Saved {len(summaries)} summaries to {output_file}")

    return output_file
