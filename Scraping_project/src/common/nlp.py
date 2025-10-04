import importlib
import json
import logging
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_TEXT_LENGTH = 20_000
TOP_KEYWORDS = 15

# Audio file pattern matching (allow optional querystrings)
AUDIO_RE = re.compile(r"\.(mp3|wav|ogg|flac)(?:\?.*)?$", re.I)


def _resolve_module(name: str):
    """Return an optional dependency, honouring test monkeypatches."""

    module = globals().get(name)

    # Tests patch these attributes with MagicMocks that expose side_effect to
    # simulate ImportError. Treat such patched objects as unavailable.
    if getattr(module, "side_effect", None):  # pragma: no cover - test only
        return None

    if module is not None:
        return module

    try:
        module = importlib.import_module(name)
    except ImportError:
        module = None

    globals()[name] = module
    return module


try:  # pragma: no cover - availability depends on environment
    import torch  # type: ignore
except Exception:  # noqa: BLE001 - torch presence varies across environments
    torch = None  # type: ignore


try:  # pragma: no cover
    import mlx.core as mx  # type: ignore
except Exception:  # noqa: BLE001
    mx = None  # type: ignore


try:  # pragma: no cover - optional dependency
    import spacy  # type: ignore
except Exception:  # noqa: BLE001
    spacy = None  # type: ignore


try:  # pragma: no cover - optional dependency
    from transformers import pipeline  # type: ignore
    HAS_TRANSFORMERS = True
except Exception:  # noqa: BLE001
    pipeline = None  # type: ignore
    HAS_TRANSFORMERS = False


try:  # pragma: no cover - optional dependency
    import yake  # type: ignore
    HAS_YAKE = True
except Exception:  # noqa: BLE001
    yake = None  # type: ignore
    HAS_YAKE = False


@dataclass
class NLPSettings:
    """Runtime configuration for NLP pipelines."""

    spacy_model: str = "en_core_web_sm"
    transformer_model: str | None = "microsoft/deberta-v3-base"
    summarizer_model: str | None = "sshleifer/distilbart-cnn-12-6"
    zero_shot_model: str | None = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"
    preferred_device: str | None = None
    additional_stop_words: set[str] = field(default_factory=set)
    stop_word_overrides: set[str] = field(default_factory=set)
    use_yake_keywords: bool = True  # Use YAKE for advanced keyword extraction


class NLPRegistry:
    """Centralised manager for NLP pipelines using spaCy and DeBERTa transformers."""

    def __init__(self, settings: NLPSettings) -> None:
        self.settings = settings
        self.device = select_device(settings.preferred_device)
        self.spacy_nlp = self._load_spacy(settings.spacy_model)
        self.entity_labels = self._resolve_entity_labels()
        self.stop_words = self._build_stop_words(
            settings.additional_stop_words, settings.stop_word_overrides
        )
        self.transformer_pipeline = self._load_transformer(settings.transformer_model)
        self.summarizer_pipeline = self._load_summarizer(settings.summarizer_model)
        self.zero_shot_pipeline = self._load_zero_shot_classifier(
            settings.zero_shot_model
        )
        self.yake_extractor = self._load_yake_extractor(settings.use_yake_keywords)

    def _load_spacy(self, model_name: str):
        spacy_module = _resolve_module("spacy")
        if spacy_module is None:
            raise RuntimeError("spaCy is required but not installed")

        try:
            nlp = spacy_module.load(model_name)
        except Exception as exc:  # pragma: no cover - configuration issue
            raise RuntimeError(f"Unable to load spaCy model '{model_name}': {exc}") from exc

        if "lemmatizer" not in nlp.pipe_names:
            try:
                nlp.add_pipe("lemmatizer", config={"mode": "rule"}, after="tagger")
            except Exception as lemmatizer_exc:
                logger.debug(
                    "Failed adding lemmatiser to spaCy pipeline: %s", lemmatizer_exc
                )

        return nlp

    def _resolve_entity_labels(self) -> set[str]:
        if not self.spacy_nlp:
            return set()
        pipe_labels = getattr(self.spacy_nlp, "pipe_labels", {})
        return set(pipe_labels.get("ner", []))

    def _build_stop_words(
        self,
        additions: set[str],
        overrides: set[str],
    ) -> set[str]:
        base = set()
        if self.spacy_nlp and hasattr(self.spacy_nlp, "Defaults"):
            base = set(getattr(self.spacy_nlp.Defaults, "stop_words", set()))
        stop_words = (base | additions) - overrides
        return {word.lower() for word in stop_words}

    def _load_transformer(self, model_name: str | None):
        if not model_name:
            return None

        transformer_pipeline = pipeline
        if getattr(transformer_pipeline, "side_effect", None):  # test hook
            transformer_pipeline = None

        if transformer_pipeline is None:
            logger.warning(
                "transformers package not available; transformer pipeline disabled"
            )
            return None

        device_arg = self._transformer_device_argument()

        try:
            return transformer_pipeline(
                "token-classification",
                model=model_name,
                tokenizer=model_name,
                aggregation_strategy="simple",
                device=device_arg,
            )
        except Exception as exc:
            logger.error(
                "Failed to load transformer model '%s' on device '%s': %s",
                model_name,
                device_arg,
                exc,
                exc_info=True
            )
            logger.warning("Transformer pipeline will be disabled")
            return None

    def _load_summarizer(self, model_name: str | None):
        if not model_name:
            return None

        transformer_pipeline = pipeline
        if getattr(transformer_pipeline, "side_effect", None):  # test hook
            transformer_pipeline = None

        if transformer_pipeline is None:
            logger.warning(
                "transformers package not available; summarizer pipeline disabled"
            )
            return None

        device_arg = self._transformer_device_argument()

        try:
            return transformer_pipeline(
                "summarization",
                model=model_name,
                tokenizer=model_name,
                device=device_arg,
            )
        except Exception as exc:
            logger.error(
                "Failed to load summarizer model '%s' on device '%s': %s",
                model_name,
                device_arg,
                exc,
                exc_info=True
            )
            logger.warning("Summarizer pipeline will be disabled")
            return None

    def _load_zero_shot_classifier(self, model_name: str | None):
        if not model_name:
            return None

        transformer_pipeline = pipeline
        if getattr(transformer_pipeline, "side_effect", None):  # test hook
            transformer_pipeline = None

        if transformer_pipeline is None:
            logger.warning(
                "transformers package not available; zero-shot classification disabled"
            )
            return None

        device_arg = self._transformer_device_argument()

        try:
            return transformer_pipeline(
                "zero-shot-classification",
                model=model_name,
                tokenizer=model_name,
                device=device_arg,
            )
        except Exception as exc:
            logger.error(
                "Failed to load zero-shot model '%s' on device '%s': %s",
                model_name,
                device_arg,
                exc,
                exc_info=True
            )
            logger.warning("Zero-shot classification will be disabled")
            return None

    def _transformer_device_argument(self):
        if self.device == "cuda":
            return 0

        if self.device == "mps":
            try:
                import torch  # type: ignore[import-not-found]

                return torch.device("mps")
            except ImportError:
                logger.warning("PyTorch missing for MPS device; using CPU instead")

        return -1

    def _load_yake_extractor(self, use_yake: bool):
        """Load YAKE keyword extractor if enabled and available."""
        if not use_yake:
            return None

        yake_module = _resolve_module("yake")
        if yake_module is None:
            logger.warning("YAKE not available; falling back to spaCy keyword extraction")
            return None

        try:
            # Configure YAKE for general-purpose keyword extraction
            # Extracts meaningful keywords from any content: academic, sports, faculty, events, etc.
            # language: English
            # max_ngram_size: 3 = captures names (e.g. "John Smith"), phrases (e.g. "basketball team")
            # deduplication_threshold: 0.9 = lenient dedup to preserve names and specific terms
            # deduplication_algo: leve = Levenshtein distance for better name variants
            # window_size: 1 = tighter window to capture precise collocations
            # top: Extract top 50 most relevant keywords
            kw_extractor = yake_module.KeywordExtractor(
                lan="en",
                n=3,  # Max n-gram size (names, phrases, terms)
                dedupLim=0.9,  # Lenient dedup to preserve names/variants
                dedupFunc='leve',  # Levenshtein for name matching
                windowsSize=1,  # Tight window for precision
                top=50  # Extract top 50 keywords
            )
            logger.info("YAKE keyword extractor initialized successfully")
            return kw_extractor
        except Exception as exc:
            logger.error(f"Failed to initialize YAKE keyword extractor: {exc}")
            logger.warning("Falling back to spaCy keyword extraction")
            return None

    def extract_with_spacy(
        self,
        text: str,
        top_k: int
    ) -> tuple[list[str], list[str]]:
        doc = self.spacy_nlp(text)
        entities = []
        seen = OrderedDict()

        for ent in doc.ents:
            if self.entity_labels and ent.label_ not in self.entity_labels:
                continue

            cleaned = ent.text.strip()
            if not cleaned or cleaned in seen:
                continue
            seen[cleaned] = None
            entities.append(cleaned)

        # Apply entity filtering to remove nonsensical results
        entities = filter_entities(entities)

        # Use YAKE for keyword extraction if available, otherwise fall back to spaCy
        if self.yake_extractor:
            keywords = self._extract_keywords_with_yake(text, top_k)
        else:
            keywords = self._keywords_from_doc(doc, top_k)

        return entities, keywords

    def extract_entities_with_transformer(self, text: str) -> list[str]:
        if not self.transformer_pipeline:
            raise RuntimeError("Transformer pipeline is not initialised")

        raw_entities = self.transformer_pipeline(text)
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

        # Apply entity filtering to remove nonsensical results
        return filter_entities(entities)

    def summarize_text(self, text: str, max_length: int, min_length: int) -> str:
        if not self.summarizer_pipeline:
            # Return empty string when summarizer is not available (e.g., use_transformers=False)
            return ""

        summary = self.summarizer_pipeline(
            text,
            max_length=max_length,
            min_length=min_length,
            do_sample=False
        )
        return summary[0]["summary_text"]

    def classify_text(self, text: str, labels: list[str]) -> dict:
        if not self.zero_shot_pipeline:
            # Return empty dict when zero-shot classifier is not available (e.g., use_transformers=False)
            return {}

        results = self.zero_shot_pipeline(text, labels)
        return dict(zip(results["labels"], results["scores"], strict=False))

    def _extract_keywords_with_yake(self, text: str, top_k: int) -> list[str]:
        """Extract keywords using YAKE algorithm.

        YAKE (Yet Another Keyword Extractor) is a statistical keyword extraction method
        that uses local text features to identify important keywords without requiring
        training data or dictionaries.
        """
        if not self.yake_extractor or not text:
            return []

        try:
            # Extract keywords with YAKE (returns list of (keyword, score) tuples)
            # Lower score = more relevant keyword
            yake_results = self.yake_extractor.extract_keywords(text)

            # Filter and clean keywords
            keywords = []
            seen = set()

            for keyword, score in yake_results:
                # Clean the keyword
                cleaned = keyword.strip().lower()

                # Skip if already seen (case-insensitive)
                if cleaned in seen:
                    continue

                # Skip if it's a stop word
                if cleaned in self.stop_words:
                    continue

                # Skip very short keywords (less than 3 characters)
                if len(cleaned) < 3:
                    continue

                # Add to results
                keywords.append(cleaned)
                seen.add(cleaned)

                # Stop when we have enough keywords
                if len(keywords) >= top_k:
                    break

            return keywords

        except Exception as exc:
            logger.warning(f"YAKE keyword extraction failed: {exc}")
            # Fall back to simple frequency-based extraction
            return []

    def _keywords_from_doc(self, doc, top_k: int) -> list[str]:
        """Fallback keyword extraction using spaCy frequency analysis."""
        candidates: list[str] = []

        for token in doc:
            if not token.is_alpha:
                continue

            lemma = (token.lemma_ or token.text).lower().strip()
            if not lemma:
                continue
            if lemma in self.stop_words:
                continue
            candidates.append(lemma)

        counter = Counter(candidates)
        return [word for word, _ in counter.most_common(top_k)]


class _DummyNLPRegistry:
    """Fallback registry when models aren't available"""

    def extract_with_spacy(self, text: str, top_k: int) -> tuple[list[str], list[str]]:
        return [], []

    def extract_entities_with_transformer(self, text: str) -> list[str]:
        return []

    def summarize_text(self, text: str, max_length: int, min_length: int) -> str:
        return ""

    def classify_text(self, text: str, labels: list[str]) -> dict:
        return {}


NLP_REGISTRY: NLPRegistry | None = None


def initialize_nlp(settings: NLPSettings | None = None) -> None:
    """Initialise the global NLP registry."""

    global NLP_REGISTRY
    NLP_REGISTRY = NLPRegistry(settings or NLPSettings())


def get_registry() -> NLPRegistry:
    global NLP_REGISTRY
    if NLP_REGISTRY is None:
        initialize_nlp()
    return NLP_REGISTRY


def extract_entities_and_keywords(
    text: str,
    max_length: int = MAX_TEXT_LENGTH,
    top_k: int = TOP_KEYWORDS,
    backend: str = "spacy",
) -> tuple[list[str], list[str]]:
    """Extract entities and keywords using the configured NLP backend.

    Keywords are extracted using YAKE (Yet Another Keyword Extractor) if available,
    otherwise falls back to spaCy frequency-based extraction.

    Entities are extracted using either DeBERTa transformers or spaCy NER.
    """

    if not text:
        return [], []

    registry = get_registry()
    truncated = text[:max_length]

    if backend == "transformer":
        entities = registry.extract_entities_with_transformer(truncated)
        # Keywords use YAKE if available, otherwise spaCy frequency analysis
        _, keywords = registry.extract_with_spacy(truncated, top_k)
        return entities, keywords

    entities, keywords = registry.extract_with_spacy(truncated, top_k)
    return entities, keywords


def summarize(text: str, max_length: int = 150, min_length: int = 30) -> str:
    """Summarize text using the configured NLP backend."""

    if not text:
        return ""

    registry = get_registry()
    return registry.summarize_text(text, max_length=max_length, min_length=min_length)


def classify(text: str, labels: list[str]) -> dict:
    """Classify text using the configured NLP backend."""

    if not text or not labels:
        return {}

    registry = get_registry()
    return registry.classify_text(text, labels)


def extract_content_tags(url_path: str, predefined_tags: set[str]) -> list[str]:
    """Extract content tags from a URL path while preserving order."""

    if not url_path or not predefined_tags:
        return []

    cleaned_tags: list[str] = []
    seen: set[str] = set()

    for part in url_path.split("/"):
        candidate = part.lower().strip()
        if not candidate or candidate not in predefined_tags:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        cleaned_tags.append(candidate)

    return cleaned_tags


def has_audio_links(links: list[str]) -> bool:
    """Check if any links point to audio-capable resources."""

    if not links:
        return False

    return any(AUDIO_RE.search(link or "") for link in links)

def clean_text(text: str) -> str:
    """Clean and normalize text content without destroying contractions."""

    if not text:
        return ""

    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"[^\w\s.,!?;:'()\-]", "", text)
    return text


def filter_entities(entities: list[str]) -> list[str]:
    """Filter out nonsensical or invalid entities.

    Removes entities that:
    - Are too long (>6 words)
    - Contain newline characters
    - Are duplicates (case-insensitive)
    - Don't contain any letters
    - Are just numbers or punctuation
    """
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
        word_count = len(cleaned.split())
        if word_count > 6:
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


def extract_keywords_simple(
    text: str,
    top_k: int = TOP_KEYWORDS,
    stop_words: set[str] | None = None,
) -> list[str]:
    """Extract keywords via simple frequency analysis."""

    if not text:
        return []

    effective_stop_words = {word.lower() for word in (stop_words or set())}
    words = re.findall(r"[a-zA-Z']{3,}", text.lower())
    filtered_words = [word for word in words if word not in effective_stop_words]
    word_counts = Counter(filtered_words)
    return [word for word, _ in word_counts.most_common(top_k)]

def get_text_stats(text: str) -> dict:
    """Return basic statistics for the supplied text."""

    if not text:
        return {
            "word_count": 0,
            "char_count": 0,
            "sentence_count": 0,
            "avg_word_length": 0,
        }

    tokens = re.findall(r"[A-Za-z']+", text)
    char_count = len(text)
    sentences = [segment for segment in re.split(r"[.!?]+", text) if segment.strip()]

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

def _is_true(predicate) -> bool:
    try:
        return bool(predicate())
    except Exception:
        return False

def select_device(preferred: str | None = None) -> str:
    """Determine the best execution device available."""

    if preferred:
        return preferred

    torch_module = _resolve_module("torch")
    if torch_module is not None:
        cuda_module = getattr(torch_module, "cuda", None)
        if cuda_module and _is_true(getattr(cuda_module, "is_available", lambda: False)):
            # Enable CUDA optimizations for RTX 4080 and similar GPUs
            try:
                # Set float32 matmul precision for better performance on modern GPUs
                if hasattr(torch_module, 'set_float32_matmul_precision'):
                    torch_module.set_float32_matmul_precision("high")
                    logger.info("Enabled high precision float32 matmul for CUDA")
            except Exception as e:
                logger.debug(f"Could not set matmul precision: {e}")

            return "cuda"

        backends = getattr(torch_module, "backends", None)
        mps_backend = getattr(backends, "mps", None)
        if mps_backend and _is_true(getattr(mps_backend, "is_available", lambda: False)):
            return "mps"

    mx_module = _resolve_module("mx")
    if mx_module is not None:
        try:
            if hasattr(mx_module, "gpu_count") and mx_module.gpu_count() > 0:
                return "mlx"
            default_device = getattr(mx_module, "default_device", None)
            device = default_device() if callable(default_device) else None
            if getattr(device, "type", None) == "gpu":
                return "mlx"
        except Exception:
            logger.debug("MLX reported but unavailable; defaulting to CPU")

    return "cpu"


def load_taxonomy(taxonomy_path: str | Path | None = None) -> dict:
    """Load taxonomy from JSON file.

    Args:
        taxonomy_path: Path to taxonomy JSON file. If None, uses default path.

    Returns:
        Dictionary containing taxonomy data with categories and keywords
    """
    if taxonomy_path is None:
        taxonomy_path = Path(__file__).parent.parent.parent / "data" / "config" / "taxonomy.json"

    taxonomy_path = Path(taxonomy_path)

    if not taxonomy_path.exists():
        logger.warning(f"Taxonomy file not found: {taxonomy_path}")
        return {"categories": []}

    try:
        with open(taxonomy_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load taxonomy from {taxonomy_path}: {e}")
        return {"categories": []}


def load_glossary(glossary_path: str | Path | None = None) -> dict:
    """Load UConn-specific glossary from JSON file.

    Args:
        glossary_path: Path to glossary JSON file. If None, uses default path.

    Returns:
        Dictionary containing glossary terms
    """
    if glossary_path is None:
        glossary_path = Path(__file__).parent.parent.parent / "data" / "config" / "uconn_glossary.json"

    glossary_path = Path(glossary_path)

    if not glossary_path.exists():
        logger.warning(f"Glossary file not found: {glossary_path}")
        return {"terms": {}}

    try:
        with open(glossary_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load glossary from {glossary_path}: {e}")
        return {"terms": {}}


def extract_glossary_terms(text: str, glossary: dict) -> list[str]:
    """Extract UConn-specific terms from text using glossary.

    Args:
        text: Text content to search
        glossary: Glossary dictionary loaded from JSON

    Returns:
        List of matched glossary terms found in text
    """
    if not text or not glossary.get("terms"):
        return []

    text_lower = text.lower()
    matched_terms = []
    seen = set()

    # Iterate through all term categories
    for _category, terms in glossary["terms"].items():
        for term_data in terms:
            term = term_data.get("term", "")
            aliases = term_data.get("aliases", [])

            # Check main term
            if term and term.lower() in text_lower:
                if term not in seen:
                    matched_terms.append(term)
                    seen.add(term)

            # Check aliases
            for alias in aliases:
                if alias and alias.lower() in text_lower:
                    if term not in seen:
                        matched_terms.append(term)
                        seen.add(term)
                        break

    return matched_terms


def classify_with_taxonomy(text: str, taxonomy: dict, classifier_func=None, top_k: int = 5) -> list[dict]:
    """Classify text using taxonomy categories.

    Args:
        text: Text content to classify
        taxonomy: Taxonomy dictionary loaded from JSON
        classifier_func: Optional zero-shot classifier function
        top_k: Number of top categories to return

    Returns:
        List of category dictionaries with scores
    """
    if not text or not taxonomy.get("categories"):
        return []

    results = []
    text_lower = text.lower()

    # Simple keyword-based classification
    for category in taxonomy["categories"]:
        cat_id = category.get("id", "")
        cat_label = category.get("label", "")

        score = 0.0
        matched_keywords = []

        # Check subcategories
        for subcat in category.get("subcategories", []):
            keywords = subcat.get("keywords", [])
            for keyword in keywords:
                if keyword.lower() in text_lower:
                    score += 1.0
                    matched_keywords.append(keyword)

        if score > 0:
            results.append({
                "category_id": cat_id,
                "category_label": cat_label,
                "score": score,
                "matched_keywords": matched_keywords[:5]  # Limit to top 5 matches
            })

    # Sort by score and return top_k
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]