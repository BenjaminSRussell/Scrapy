"""
Simple, Reliable NLP Processor

Uses proven models that actually work.
Provides basic NLP functionality with spaCy for fallback and simple use cases.
For production, use the full NLP pipeline in nlp.py with DeBERTa transformer models.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class NLPResult:
    """NLP processing results"""
    entities: list[str]
    keywords: list[str]
    categories: list[str]
    word_count: int
    sentence_count: int


class SimpleNLPProcessor:
    """
    Lightweight NLP processor using spaCy for basic tasks.

    This is a fallback/simple processor. For production enrichment,
    use the full NLP pipeline with DeBERTa transformer models.
    """

    def __init__(self):
        self._nlp = None
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize spaCy model"""
        if self._initialized:
            return True

        try:
            import spacy
            self._nlp = spacy.load("en_core_web_sm")
            self._initialized = True
            logger.info("spaCy NLP initialized")
            return True
        except (ImportError, OSError) as e:
            logger.error(f"Failed to load spaCy: {e}")
            logger.error("Run: python -m spacy download en_core_web_sm")
            return False

    def extract_entities(self, text: str, max_length: int = 100000) -> list[str]:
        """Extract named entities"""
        if not self._initialized:
            return []

        try:
            # Limit text length
            text = text[:max_length]
            doc = self._nlp(text)

            # Extract unique entities
            entities = list(set([
                ent.text.strip()
                for ent in doc.ents
                if len(ent.text.strip()) > 2  # Filter short entities
            ]))

            return entities[:30]  # Top 30 entities

        except Exception as e:
            logger.error(f"Entity extraction failed: {e}")
            return []

    def extract_keywords(self, text: str, top_n: int = 15) -> list[str]:
        """Extract keywords using frequency and POS tagging"""
        if not self._initialized:
            return self._simple_keywords(text, top_n)

        try:
            doc = self._nlp(text.lower())

            # Filter for nouns and proper nouns
            words = [
                token.lemma_
                for token in doc
                if token.pos_ in ('NOUN', 'PROPN')
                and len(token.text) > 3
                and not token.is_stop
                and token.is_alpha
            ]

            # Count frequencies
            counter = Counter(words)
            return [word for word, _ in counter.most_common(top_n)]

        except Exception as e:
            logger.error(f"Keyword extraction failed: {e}")
            return self._simple_keywords(text, top_n)

    def _simple_keywords(self, text: str, top_n: int) -> list[str]:
        """Fallback keyword extraction"""
        words = re.findall(r'\b[a-z]{4,}\b', text.lower())

        stop_words = {
            'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have',
            'i', 'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you',
            'do', 'at', 'this', 'but', 'his', 'by', 'from', 'they',
            'are', 'was', 'will', 'would', 'been', 'their'
        }

        filtered = [w for w in words if w not in stop_words]
        counter = Counter(filtered)
        return [word for word, _ in counter.most_common(top_n)]

    def classify_categories(self, text: str, keywords: list[str]) -> list[str]:
        """Simple category classification based on keywords"""
        text_lower = text.lower()
        categories = []

        # Educational keywords
        if any(kw in text_lower for kw in ['course', 'class', 'program', 'degree', 'student', 'academic', 'education']):
            categories.append('education')

        # Research keywords
        if any(kw in text_lower for kw in ['research', 'study', 'lab', 'laboratory', 'science', 'investigation']):
            categories.append('research')

        # Administrative keywords
        if any(kw in text_lower for kw in ['office', 'department', 'staff', 'administration', 'service', 'support']):
            categories.append('administrative')

        # Athletics keywords
        if any(kw in text_lower for kw in ['sport', 'team', 'athletic', 'game', 'basketball', 'football']):
            categories.append('athletics')

        # Healthcare keywords
        if any(kw in text_lower for kw in ['health', 'medical', 'clinic', 'hospital', 'patient', 'doctor']):
            categories.append('healthcare')

        return categories

    def count_sentences(self, text: str) -> int:
        """Count sentences"""
        if self._initialized:
            try:
                doc = self._nlp(text)
                return len(list(doc.sents))
            except Exception:
                pass

        # Fallback
        return len(re.split(r'[.!?]+', text))

    def process(self, text: str) -> NLPResult:
        """
        Process text and extract all NLP features

        Args:
            text: Input text

        Returns:
            NLPResult with extracted information
        """
        if not self._initialized:
            self.initialize()

        # Basic stats
        word_count = len(text.split())
        sentence_count = self.count_sentences(text)

        # Extract features
        entities = self.extract_entities(text)
        keywords = self.extract_keywords(text)
        categories = self.classify_categories(text, keywords)

        return NLPResult(
            entities=entities,
            keywords=keywords,
            categories=categories,
            word_count=word_count,
            sentence_count=sentence_count
        )


# Singleton instance
_nlp_processor: SimpleNLPProcessor | None = None


def get_nlp_processor() -> SimpleNLPProcessor:
    """Get singleton NLP processor instance"""
    global _nlp_processor
    if _nlp_processor is None:
        _nlp_processor = SimpleNLPProcessor()
        _nlp_processor.initialize()
    return _nlp_processor
