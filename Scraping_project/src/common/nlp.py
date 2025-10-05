"""
Simplified NLP using YAKE for keywords and DeBerta for classification.
No spaCy dependency.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def extract_keywords(text: str, max_keywords: int = 15) -> list[str]:
    """Extract keywords using YAKE."""
    try:
        import yake

        kw_extractor = yake.KeywordExtractor(
            lan="en",
            n=3,  # max ngram size
            dedupLim=0.9,
            top=max_keywords,
            features=None
        )

        keywords = kw_extractor.extract_keywords(text)
        return [kw[0] for kw in keywords]
    except ImportError:
        logger.warning("YAKE not installed, skipping keyword extraction")
        return []
    except Exception as e:
        logger.error(f"Keyword extraction failed: {e}")
        return []


def classify_content(text: str) -> dict[str, Any]:
    """Classify content using DeBerta."""
    try:
        from transformers import pipeline
        from src.common.constants import DEBERTA_MODEL

        classifier = pipeline(
            "zero-shot-classification",
            model=DEBERTA_MODEL,
            device=-1  # CPU
        )

        candidate_labels = [
            "academic",
            "admissions",
            "research",
            "student services",
            "administration",
            "events",
            "news"
        ]

        result = classifier(text[:512], candidate_labels)  # Limit text length

        return {
            "primary_category": result["labels"][0],
            "confidence": result["scores"][0],
            "categories": dict(zip(result["labels"], result["scores"]))
        }
    except ImportError:
        logger.warning("Transformers not installed, skipping classification")
        return {"primary_category": "unknown", "confidence": 0.0, "categories": {}}
    except Exception as e:
        logger.error(f"Classification failed: {e}")
        return {"primary_category": "error", "confidence": 0.0, "categories": {}}


def process_text(text: str, extract_kw: bool = True, classify: bool = True) -> dict[str, Any]:
    """Process text with NLP."""
    result = {
        "keywords": [],
        "classification": {}
    }

    if extract_kw:
        result["keywords"] = extract_keywords(text)

    if classify:
        result["classification"] = classify_content(text)

    return result
