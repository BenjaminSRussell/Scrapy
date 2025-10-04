"""
NLP Manager - Unified NLP Processing with DeBERTa

Handles all NLP operations with proper class structure.
Single entry point for entity extraction, classification, and text processing.
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class NLPResult:
    """Results from NLP processing"""
    entities: list[str]
    keywords: list[str]
    categories: list[str]
    summary: str | None = None
    confidence_scores: dict[str, float] | None = None


class DeBERTaNLPProcessor:
    """
    DeBERTa-based NLP processor for content enrichment.

    Uses microsoft/deberta-v3-base for NER and
    MoritzLaurer/deberta-v3-base-zeroshot-v2.0 for classification.
    """

    def __init__(self, device: str = "cpu"):
        self.device = device
        self._ner_pipeline = None
        self._zero_shot_pipeline = None
        self._initialized = False

    def initialize(self) -> None:
        """Lazy initialization of transformers models"""
        if self._initialized:
            return

        try:
            from transformers import pipeline

            logger.info("Initializing DeBERTa NLP pipelines...")

            # NER pipeline
            self._ner_pipeline = pipeline(
                "token-classification",
                model="microsoft/deberta-v3-base",
                aggregation_strategy="simple",
                device=self.device
            )
            logger.info("DeBERTa NER pipeline initialized")

            # Zero-shot classification
            self._zero_shot_pipeline = pipeline(
                "zero-shot-classification",
                model="MoritzLaurer/deberta-v3-base-zeroshot-v2.0",
                device=self.device
            )
            logger.info("DeBERTa zero-shot pipeline initialized")

            self._initialized = True

        except ImportError as e:
            logger.error(f"Failed to import transformers: {e}")
            logger.warning("NLP processing will be disabled")
        except Exception as e:
            logger.error(f"Failed to initialize NLP pipelines: {e}")
            logger.warning("NLP processing will be disabled")

    def extract_entities(self, text: str, max_length: int = 512) -> list[str]:
        """Extract named entities using DeBERTa"""
        if not self._initialized or not self._ner_pipeline:
            return []

        try:
            # Truncate text to model limit
            truncated_text = text[:max_length]

            results = self._ner_pipeline(truncated_text)

            # Extract unique entity texts
            entities = list(set([
                result['word'].strip()
                for result in results
                if result.get('score', 0) > 0.5  # Confidence threshold
            ]))

            return entities[:20]  # Top 20 entities

        except Exception as e:
            logger.error(f"Entity extraction failed: {e}")
            return []

    def classify_content(
        self,
        text: str,
        candidate_labels: list[str],
        max_length: int = 512
    ) -> dict[str, float]:
        """Classify content using zero-shot classification"""
        if not self._initialized or not self._zero_shot_pipeline:
            return {}

        try:
            truncated_text = text[:max_length]

            result = self._zero_shot_pipeline(
                truncated_text,
                candidate_labels=candidate_labels,
                multi_label=True
            )

            # Return label -> score mapping
            scores = dict(zip(result['labels'], result['scores'], strict=False))

            # Filter to confident predictions (> 0.3)
            confident_scores = {
                label: score
                for label, score in scores.items()
                if score > 0.3
            }

            return confident_scores

        except Exception as e:
            logger.error(f"Content classification failed: {e}")
            return {}

    def extract_keywords(self, text: str, top_n: int = 10) -> list[str]:
        """Extract keywords from text (simple frequency-based)"""
        import re
        from collections import Counter

        # Simple tokenization
        words = re.findall(r'\b[a-z]{3,}\b', text.lower())

        # Common stop words
        stop_words = {
            'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have',
            'i', 'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you',
            'do', 'at', 'this', 'but', 'his', 'by', 'from', 'they'
        }

        # Filter and count
        filtered_words = [w for w in words if w not in stop_words]
        counter = Counter(filtered_words)

        return [word for word, _ in counter.most_common(top_n)]

    def process(
        self,
        text: str,
        categories: list[str] | None = None
    ) -> NLPResult:
        """
        Complete NLP processing pipeline

        Args:
            text: Input text to process
            categories: Optional list of categories for classification

        Returns:
            NLPResult with all extracted information
        """
        if not self._initialized:
            self.initialize()

        entities = self.extract_entities(text)
        keywords = self.extract_keywords(text)

        if categories:
            confidence_scores = self.classify_content(text, categories)
            # Get categories with confidence > 0.3
            classified_categories = [
                cat for cat, score in confidence_scores.items()
                if score > 0.3
            ]
        else:
            confidence_scores = None
            classified_categories = []

        return NLPResult(
            entities=entities,
            keywords=keywords,
            categories=classified_categories,
            confidence_scores=confidence_scores
        )


class NLPManager:
    """Singleton NLP Manager"""

    _instance: Optional['NLPManager'] = None
    _processor: DeBERTaNLPProcessor | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._processor = DeBERTaNLPProcessor()
        return cls._instance

    @property
    def processor(self) -> DeBERTaNLPProcessor:
        """Get NLP processor instance"""
        if self._processor is None:
            self._processor = DeBERTaNLPProcessor()
        return self._processor

    def process_text(self, text: str, categories: list[str] | None = None) -> NLPResult:
        """Process text with NLP"""
        return self.processor.process(text, categories)

    def initialize(self) -> None:
        """Initialize NLP models (lazy loading)"""
        self.processor.initialize()
