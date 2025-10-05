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
            import torch
            from transformers import pipeline

            logger.info("Initializing NLP pipelines...")

            # Set appropriate device
            if self.device == "auto":
                self.device = "cuda" if torch.cuda.is_available() else "cpu"
                logger.info(f"Using device: {self.device}")

            # NER pipeline - try multiple models in order of preference
            ner_models = [
                ("microsoft/deberta-v3-base", "microsoft/deberta-v3-base-finetuned-ner"),
                ("dslim/bert-base-NER", None),  # Fallback option
                ("dbmdz/bert-large-cased-finetuned-conll03-english", None)  # Last resort
            ]

            for base_model, finetuned in ner_models:
                try:
                    if finetuned:
                        # Try to use a fine-tuned version first
                        self._ner_pipeline = pipeline(
                            "token-classification",
                            model=finetuned,
                            aggregation_strategy="simple",
                            device=self.device
                        )
                    else:
                        # Use base model
                        self._ner_pipeline = pipeline(
                            "token-classification",
                            model=base_model,
                            aggregation_strategy="simple",
                            device=self.device
                        )
                    logger.info(f"NER pipeline initialized using {base_model}")
                    break
                except Exception as e:
                    logger.warning(f"Failed to load NER model {base_model}: {e}")
                    continue

            if self._ner_pipeline is None:
                logger.error("Failed to initialize any NER pipeline")
                return

            # Zero-shot classification pipeline
            try:
                self._zero_shot_pipeline = pipeline(
                    "zero-shot-classification",
                    model="MoritzLaurer/deberta-v3-base-zeroshot-v2.0",
                    device=self.device
                )
                logger.info("Zero-shot classification pipeline initialized successfully.")
            except Exception as e:
                logger.warning(f"Failed to load zero-shot pipeline: {e}")
                self._zero_shot_pipeline = None

            # Set initialized to True if at least one pipeline loaded
            self._initialized = (self._ner_pipeline is not None or self._zero_shot_pipeline is not None)

            if self._initialized:
                logger.info("NLP pipelines initialized successfully")
            else:
                logger.warning("No NLP pipelines could be initialized")

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

            # Extract unique entity texts with better filtering
            entities = []
            seen = set()

            for result in results:
                # Skip low confidence results
                if result.get('score', 0) < 0.5:
                    continue

                # Clean and normalize the entity text
                entity = result['word'].strip()

                # Skip if already seen or invalid
                if (entity.lower() in seen or 
                    len(entity) <= 1 or  # Skip single characters
                    entity.isnumeric() or  # Skip pure numbers
                    all(c.isspace() for c in entity)):  # Skip whitespace
                    continue

                # Handle special case for 'UConn'
                if 'uconn' in entity.lower() or 'connecticut' in entity.lower():
                    entities.append(entity)
                    seen.add(entity.lower())
                    continue

                # Common institution-related terms should be included
                institutional_terms = {'university', 'college', 'department', 'faculty', 'research', 'program'}
                if (any(term in entity.lower() for term in institutional_terms) or
                    result.get('entity_group', '').lower() in {'org', 'institution', 'location'}):
                    entities.append(entity)
                    seen.add(entity.lower())
                    continue

                # For other cases, require higher confidence
                if result.get('score', 0) > 0.7:
                    entities.append(entity)
                    seen.add(entity.lower())

            # Sort by length (prefer longer entities) and return top 20
            return sorted(entities, key=len, reverse=True)[:20]

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

            # Use the zero-shot pipeline
            pipeline_result = self._zero_shot_pipeline(truncated_text, candidate_labels, truncation=True)

            # The pipeline returns a dict with 'labels' and 'scores'
            if 'labels' in pipeline_result and 'scores' in pipeline_result:
                return dict(zip(pipeline_result['labels'], pipeline_result['scores']))

            return {}

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
