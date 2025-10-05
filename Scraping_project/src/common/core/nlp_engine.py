"""
Consolidated NLP Engine - DeBERTa + YAKE

Merges all NLP functionality into a single class-based system.
REMOVED: spaCy dependency (unreliable, heavyweight)
KEPT: DeBERTa transformers (microsoft/deberta-v3-base) + YAKE keyword extraction

Consolidates:
- nlp.py
- nlp_manager.py
- nlp_processor.py
- keyword_expansion.py
"""

import importlib
import logging
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 20_000
TOP_KEYWORDS = 15

# Audio file pattern matching
AUDIO_RE = re.compile(r"\.(mp3|wav|ogg|flac)(?:\?.*)?$", re.I)


def _resolve_module(name: str):
    """Return an optional dependency, honouring test monkeypatches."""
    module = globals().get(name)
    if getattr(module, "side_effect", None):
        return None
    if module is not None:
        return module
    try:
        module = importlib.import_module(name)
    except ImportError:
        module = None
    globals()[name] = module
    return module


try:
    import torch
except Exception:
    torch = None

try:
    from transformers import pipeline
    HAS_TRANSFORMERS = True
except Exception:
    pipeline = None
    HAS_TRANSFORMERS = False

try:
    import yake
    HAS_YAKE = True
except Exception:
    yake = None
    HAS_YAKE = False

try:
    import nltk
    from nltk.corpus import wordnet
    HAS_NLTK = True
except Exception:
    nltk = None
    wordnet = None
    HAS_NLTK = False


@dataclass
class NLPSettings:
    """Runtime configuration for NLP pipelines."""
    ner_model: str = "dslim/bert-base-NER"  # Actually trained for NER
    classification_model: str = "distilbert-base-uncased"  # Available offline
    summarizer_model: str | None = None  # Disabled - not needed
    preferred_device: str | None = None
    use_yake_keywords: bool = True
    use_wordnet_expansion: bool = False


@dataclass
class NLPResult:
    """Results from NLP processing"""
    entities: list[str]
    keywords: list[str]
    categories: list[str] = field(default_factory=list)
    summary: str | None = None
    confidence_scores: dict[str, float] | None = None
    word_count: int = 0
    sentence_count: int = 0
    expanded_keywords: dict[str, list[str]] | None = None


class NLPEngine:
    """
    Unified NLP Engine using DeBERTa transformers and YAKE keyword extraction.

    This consolidates all NLP functionality into a single class-based system.
    REMOVED: spaCy (unreliable, heavyweight, causes version conflicts)
    USES: DeBERTa for NER/classification, YAKE for keyword extraction
    """

    def __init__(self, settings: NLPSettings | None = None):
        self.settings = settings or NLPSettings()
        self.device = self._select_device(self.settings.preferred_device)

        # Transformer pipelines
        self.transformer_pipeline = None
        self.summarizer_pipeline = None
        self.zero_shot_pipeline = None
        self.yake_extractor = None

        self._initialized = False

    def _select_device(self, preferred: str | None = None) -> str:
        """Determine the best execution device available."""
        if preferred:
            return preferred

        torch_module = _resolve_module("torch")
        if torch_module is not None:
            cuda_module = getattr(torch_module, "cuda", None)
            if cuda_module and hasattr(cuda_module, "is_available"):
                try:
                    if cuda_module.is_available():
                        # Enable CUDA optimizations for modern GPUs
                        if hasattr(torch_module, 'set_float32_matmul_precision'):
                            torch_module.set_float32_matmul_precision("high")
                            logger.info("Enabled high precision float32 matmul for CUDA")
                        return "cuda"
                except Exception as e:
                    logger.debug(f"CUDA check failed: {e}")

            backends = getattr(torch_module, "backends", None)
            mps_backend = getattr(backends, "mps", None)
            if mps_backend and hasattr(mps_backend, "is_available"):
                try:
                    if mps_backend.is_available():
                        return "mps"
                except Exception:
                    pass

        return "cpu"

    def initialize(self) -> None:
        """Lazy initialization of NLP models"""
        if self._initialized:
            return

        logger.info(f"Initializing NLP Engine on device: {self.device}")

        # Load NER transformer pipeline
        if HAS_TRANSFORMERS and self.settings.ner_model:
            self.transformer_pipeline = self._load_transformer(
                self.settings.ner_model
            )

        # Load summarizer (disabled by default)
        if HAS_TRANSFORMERS and self.settings.summarizer_model:
            self.summarizer_pipeline = self._load_summarizer(
                self.settings.summarizer_model
            )

        # Load zero-shot classifier (offline-capable)
        if HAS_TRANSFORMERS and self.settings.classification_model:
            self.zero_shot_pipeline = self._load_zero_shot_classifier(
                self.settings.classification_model
            )

        # Load YAKE keyword extractor
        if HAS_YAKE and self.settings.use_yake_keywords:
            self.yake_extractor = self._load_yake_extractor()

        self._initialized = True
        logger.info("NLP Engine initialized successfully")

    def _transformer_device_argument(self):
        """Get device argument for transformers pipeline"""
        if self.device == "cuda":
            return 0
        if self.device == "mps":
            try:
                import torch
                return torch.device("mps")
            except ImportError:
                logger.warning("PyTorch missing for MPS device; using CPU instead")
        return -1

    def _load_transformer(self, model_name: str):
        """Load DeBERTa transformer for NER"""
        if not pipeline:
            logger.warning("transformers package not available; NER disabled")
            return None

        device_arg = self._transformer_device_argument()

        try:
            return pipeline(
                "token-classification",
                model=model_name,
                tokenizer=model_name,
                aggregation_strategy="simple",
                device=device_arg,
            )
        except Exception as exc:
            logger.error(
                f"Failed to load transformer model '{model_name}': {exc}",
                exc_info=True
            )
            return None

    def _load_summarizer(self, model_name: str):
        """Load summarizer model"""
        if not pipeline:
            return None

        device_arg = self._transformer_device_argument()

        try:
            return pipeline(
                "summarization",
                model=model_name,
                tokenizer=model_name,
                device=device_arg,
            )
        except Exception as exc:
            logger.error(f"Failed to load summarizer '{model_name}': {exc}")
            return None

    def _load_zero_shot_classifier(self, model_name: str):
        """Load zero-shot classification model (offline-capable)"""
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            # Use distilbert for offline text classification
            tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=True)
            model = AutoModelForSequenceClassification.from_pretrained(
                model_name,
                local_files_only=True,
                num_labels=2
            )

            def zero_shot_classify(text: str, labels: list[str]) -> dict[str, float]:
                """Custom zero-shot classification"""
                scores = {}
                for label in labels:
                    combined = f"{text} This is about {label}."
                    inputs = tokenizer(combined, return_tensors="pt", truncation=True, max_length=512)
                    outputs = model(**inputs)
                    probs = torch.nn.functional.softmax(outputs.logits, dim=-1)
                    scores[label] = float(probs[0][1])
                return scores

            logger.info("Zero-shot classification initialized (offline mode)")
            return zero_shot_classify
        except Exception as exc:
            logger.error(f"Failed to load zero-shot model '{model_name}': {exc}")
            return None

    def _load_yake_extractor(self):
        """Load YAKE keyword extractor"""
        yake_module = _resolve_module("yake")
        if yake_module is None:
            logger.warning("YAKE not available; keyword extraction will be basic")
            return None

        try:
            # Configure YAKE for general-purpose keyword extraction
            kw_extractor = yake_module.KeywordExtractor(
                lan="en",
                n=3,  # Max n-gram size (names, phrases, terms)
                dedupLim=0.9,  # Lenient dedup to preserve names/variants
                dedupFunc='leve',  # Levenshtein for name matching
                windowsSize=1,  # Tight window for precision
                top=50  # Extract top 50 keywords
            )
            logger.info("YAKE keyword extractor initialized")
            return kw_extractor
        except Exception as exc:
            logger.error(f"Failed to initialize YAKE: {exc}")
            return None

    def extract_entities(self, text: str, max_length: int = 512) -> list[str]:
        """Extract named entities using DeBERTa"""
        if not self._initialized:
            self.initialize()

        if not self.transformer_pipeline or not text:
            return []

        try:
            truncated = text[:max_length]
            raw_entities = self.transformer_pipeline(truncated)

            entities: list[str] = []
            seen = OrderedDict()

            for item in raw_entities:
                label_text = item.get("word") or item.get("entity")
                if not label_text:
                    continue
                cleaned = label_text.strip()
                if not cleaned or cleaned in seen:
                    continue
                seen[cleaned] = None
                entities.append(cleaned)

            return self._filter_entities(entities)

        except Exception as exc:
            logger.error(f"Entity extraction failed: {exc}")
            return []

    def extract_keywords(self, text: str, top_k: int = TOP_KEYWORDS) -> list[str]:
        """Extract keywords using YAKE or fallback to frequency analysis"""
        if not self._initialized:
            self.initialize()

        if not text:
            return []

        # Use YAKE if available
        if self.yake_extractor:
            return self._extract_keywords_yake(text, top_k)

        # Fallback to simple frequency-based extraction
        return self._extract_keywords_simple(text, top_k)

    def _extract_keywords_yake(self, text: str, top_k: int) -> list[str]:
        """Extract keywords using YAKE algorithm"""
        if not self.yake_extractor:
            return []

        try:
            yake_results = self.yake_extractor.extract_keywords(text)

            keywords = []
            seen = set()

            for keyword, score in yake_results:
                cleaned = keyword.strip().lower()

                if cleaned in seen or len(cleaned) < 3:
                    continue

                keywords.append(cleaned)
                seen.add(cleaned)

                if len(keywords) >= top_k:
                    break

            return keywords

        except Exception as exc:
            logger.warning(f"YAKE keyword extraction failed: {exc}")
            return []

    def _extract_keywords_simple(self, text: str, top_k: int) -> list[str]:
        """Simple frequency-based keyword extraction"""
        stop_words = {
            'the', 'be', 'to', 'of', 'and', 'a', 'in', 'that', 'have',
            'i', 'it', 'for', 'not', 'on', 'with', 'he', 'as', 'you',
            'do', 'at', 'this', 'but', 'his', 'by', 'from', 'they',
            'are', 'was', 'will', 'would', 'been', 'their'
        }

        words = re.findall(r'[a-zA-Z\']{3,}', text.lower())
        filtered_words = [word for word in words if word not in stop_words]
        word_counts = Counter(filtered_words)

        return [word for word, _ in word_counts.most_common(top_k)]

    def summarize_text(self, text: str, max_length: int = 150, min_length: int = 30) -> str:
        """Summarize text using transformer model"""
        if not self._initialized:
            self.initialize()

        if not self.summarizer_pipeline or not text:
            return ""

        try:
            summary = self.summarizer_pipeline(
                text,
                max_length=max_length,
                min_length=min_length,
                do_sample=False
            )
            return summary[0]["summary_text"]
        except Exception as exc:
            logger.error(f"Text summarization failed: {exc}")
            return ""

    def classify_text(self, text: str, labels: list[str]) -> dict[str, float]:
        """Classify text using zero-shot classification"""
        if not self._initialized:
            self.initialize()

        if not self.zero_shot_pipeline or not text or not labels:
            return {}

        try:
            # Call custom zero-shot function (returns dict directly)
            if callable(self.zero_shot_pipeline):
                return self.zero_shot_pipeline(text, labels)
            else:
                # Fallback for pipeline objects
                results = self.zero_shot_pipeline(text, labels, multi_label=True)
                return dict(zip(results["labels"], results["scores"], strict=False))
        except Exception as exc:
            logger.error(f"Text classification failed: {exc}")
            return {}

    def expand_keywords(self, keywords: list[str]) -> dict[str, list[str]]:
        """
        Expand keywords with synonyms using WordNet.

        Args:
            keywords: List of keywords to expand

        Returns:
            Dictionary mapping keywords to their synonyms
        """
        if not self.settings.use_wordnet_expansion or not HAS_NLTK:
            return {}

        # Download WordNet if needed
        try:
            nltk.data.find('corpora/wordnet.zip')
        except Exception:
            try:
                nltk.download('wordnet', quiet=True)
            except Exception as e:
                logger.warning(f"Failed to download WordNet: {e}")
                return {}

        expanded_keywords = {}
        for keyword in keywords:
            synonyms = set()
            try:
                for syn in wordnet.synsets(keyword):
                    for lemma in syn.lemmas():
                        synonyms.add(lemma.name().replace('_', ' '))

                if synonyms:
                    expanded_keywords[keyword] = list(synonyms)
            except Exception as e:
                logger.debug(f"Failed to expand keyword '{keyword}': {e}")

        return expanded_keywords

    def process(
        self,
        text: str,
        categories: list[str] | None = None,
        extract_summary: bool = False,
        expand_keywords: bool = False
    ) -> NLPResult:
        """
        Complete NLP processing pipeline

        Args:
            text: Input text to process
            categories: Optional list of categories for classification
            extract_summary: Whether to generate text summary
            expand_keywords: Whether to expand keywords with synonyms

        Returns:
            NLPResult with all extracted information
        """
        if not self._initialized:
            self.initialize()

        if not text:
            return NLPResult(entities=[], keywords=[], word_count=0, sentence_count=0)

        # Extract entities and keywords
        entities = self.extract_entities(text)
        keywords = self.extract_keywords(text)

        # Classification
        if categories:
            confidence_scores = self.classify_text(text, categories)
            classified_categories = [
                cat for cat, score in confidence_scores.items()
                if score > 0.3
            ]
        else:
            confidence_scores = None
            classified_categories = []

        # Summary
        summary = None
        if extract_summary:
            summary = self.summarize_text(text)

        # Text stats
        word_count = len(text.split())
        sentence_count = len(re.split(r'[.!?]+', text))

        # Keyword expansion
        expanded_kw = None
        if expand_keywords and keywords:
            expanded_kw = self.expand_keywords(keywords)

        return NLPResult(
            entities=entities,
            keywords=keywords,
            categories=classified_categories,
            summary=summary,
            confidence_scores=confidence_scores,
            word_count=word_count,
            sentence_count=sentence_count,
            expanded_keywords=expanded_kw
        )

    def _filter_entities(self, entities: list[str]) -> list[str]:
        """Filter out nonsensical or invalid entities"""
        if not entities:
            return []

        filtered = []
        seen = set()

        for entity in entities:
            if not entity or not entity.strip():
                continue

            # Remove newlines and excessive whitespace
            cleaned = re.sub(r'\s+', ' ', entity.strip())

            # Skip if contains newlines (before cleaning)
            if '\n' in entity or '\r' in entity:
                continue

            # Skip if too long (more than 6 words)
            if len(cleaned.split()) > 6:
                continue

            # Skip if doesn't contain any letters
            if not re.search(r'[a-zA-Z]', cleaned):
                continue

            # Skip if it's just numbers or punctuation
            if re.match(r'^[\d\s\W]+$', cleaned):
                continue

            # Deduplicate (case-insensitive)
            lower = cleaned.lower()
            if lower in seen:
                continue

            seen.add(lower)
            filtered.append(cleaned)

        return filtered


# Utility functions

def clean_text(text: str) -> str:
    """Clean and normalize text content"""
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"[^\w\s.,!?;:'()\-]", "", text)
    return text


def has_audio_links(links: list[str]) -> bool:
    """Check if any links point to audio resources"""
    if not links:
        return False
    return any(AUDIO_RE.search(link or "") for link in links)


def get_text_stats(text: str) -> dict:
    """Return basic statistics for text"""
    if not text:
        return {
            "word_count": 0,
            "char_count": 0,
            "sentence_count": 0,
            "avg_word_length": 0,
        }

    tokens = re.findall(r"[A-Za-z']+", text)
    char_count = len(text)
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]

    avg_word_length = (
        sum(len(token) for token in tokens) / len(tokens)
        if tokens
        else 0
    )

    return {
        "word_count": len(tokens),
        "char_count": char_count,
        "sentence_count": len(sentences),
        "avg_word_length": avg_word_length,
    }


# Global singleton instance
_nlp_engine: NLPEngine | None = None


def get_nlp_engine(settings: NLPSettings | None = None) -> NLPEngine:
    """Get global NLP engine instance (singleton)"""
    global _nlp_engine
    if _nlp_engine is None:
        _nlp_engine = NLPEngine(settings or NLPSettings())
        _nlp_engine.initialize()
    return _nlp_engine


def extract_entities_and_keywords(
    text: str,
    max_length: int = MAX_TEXT_LENGTH,
    top_k: int = TOP_KEYWORDS,
) -> tuple[list[str], list[str]]:
    """
    Extract entities and keywords (convenience function)

    Args:
        text: Input text
        max_length: Maximum text length to process
        top_k: Number of top keywords to return

    Returns:
        Tuple of (entities, keywords)
    """
    engine = get_nlp_engine()
    truncated = text[:max_length]

    entities = engine.extract_entities(truncated)
    keywords = engine.extract_keywords(truncated, top_k)

    return entities, keywords
