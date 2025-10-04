"""
Text summarization for long-form content.

Handles videos, long documents, and other verbose content.
"""

import logging
from typing import Optional

from src.common.constants import SUMMARIZATION_MODEL

logger = logging.getLogger(__name__)

# Lazy load heavy models
_summarizer = None
_tokenizer = None


def get_summarizer():
    """Lazy load summarization model."""
    global _summarizer, _tokenizer

    if _summarizer is None:
        try:
            from transformers import pipeline
            logger.info(f"Loading summarization model: {SUMMARIZATION_MODEL}")
            _summarizer = pipeline(
                "summarization",
                model=SUMMARIZATION_MODEL,
                device=-1  # CPU (use 0 for GPU)
            )
            logger.info("Summarization model loaded successfully")
        except ImportError:
            logger.error("transformers not installed: pip install transformers torch")
            raise
        except Exception as e:
            logger.error(f"Failed to load summarization model: {e}")
            raise

    return _summarizer


def summarize_text(
    text: str,
    max_length: int = 150,
    min_length: int = 50,
    do_sample: bool = False
) -> Optional[str]:
    """
    Summarize long text into a concise paragraph.

    Args:
        text: Text to summarize
        max_length: Maximum summary length in tokens
        min_length: Minimum summary length in tokens
        do_sample: Whether to use sampling (False = deterministic)

    Returns:
        Summary text or None if summarization fails
    """
    if not text or len(text.strip()) < 100:
        return text  # Too short to summarize

    try:
        summarizer = get_summarizer()

        # BART has 1024 token limit, chunk if needed
        max_input_length = 1024
        if len(text.split()) > max_input_length:
            # Split into chunks and summarize each
            chunks = _chunk_text(text, max_input_length)
            summaries = []

            for chunk in chunks:
                result = summarizer(
                    chunk,
                    max_length=max_length,
                    min_length=min_length,
                    do_sample=do_sample
                )
                summaries.append(result[0]['summary_text'])

            # Combine chunk summaries
            combined = " ".join(summaries)

            # If still too long, summarize again
            if len(combined.split()) > max_length:
                result = summarizer(
                    combined,
                    max_length=max_length,
                    min_length=min_length,
                    do_sample=do_sample
                )
                return result[0]['summary_text']

            return combined
        else:
            result = summarizer(
                text,
                max_length=max_length,
                min_length=min_length,
                do_sample=do_sample
            )
            return result[0]['summary_text']

    except Exception as e:
        logger.error(f"Summarization failed: {e}")
        # Fallback: return first N sentences
        return _simple_summary(text, max_sentences=3)


def _chunk_text(text: str, max_words: int) -> list[str]:
    """Split text into chunks by sentence boundaries."""
    sentences = text.replace('!', '.').replace('?', '.').split('.')
    chunks = []
    current_chunk = []
    current_length = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        sentence_length = len(sentence.split())

        if current_length + sentence_length > max_words:
            if current_chunk:
                chunks.append('. '.join(current_chunk) + '.')
                current_chunk = [sentence]
                current_length = sentence_length
        else:
            current_chunk.append(sentence)
            current_length += sentence_length

    if current_chunk:
        chunks.append('. '.join(current_chunk) + '.')

    return chunks


def _simple_summary(text: str, max_sentences: int = 3) -> str:
    """Fallback: Extract first N sentences."""
    sentences = text.replace('!', '.').replace('?', '.').split('.')
    summary_sentences = [s.strip() for s in sentences[:max_sentences] if s.strip()]
    return '. '.join(summary_sentences) + '.'


def summarize_video_transcript(transcript: str) -> str:
    """Summarize video transcript into a paragraph."""
    return summarize_text(transcript, max_length=200, min_length=75)


def summarize_pdf_content(text: str) -> str:
    """Summarize PDF OCR output into a paragraph."""
    return summarize_text(text, max_length=150, min_length=50)


def summarize_audio_transcript(transcript: str) -> str:
    """Summarize audio transcription into a paragraph."""
    return summarize_text(transcript, max_length=150, min_length=50)
