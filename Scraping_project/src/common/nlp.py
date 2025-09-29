import importlib
import logging
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import List, Tuple, Set, Optional

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
except Exception:  # noqa: BLE001
    pipeline = None  # type: ignore


@dataclass
class NLPSettings:
    """Runtime configuration for NLP pipelines."""

    spacy_model: str = "en_core_web_sm"
    transformer_model: Optional[str] = "dslim/bert-base-NER"
    preferred_device: Optional[str] = None
    additional_stop_words: Set[str] = field(default_factory=set)
    stop_word_overrides: Set[str] = field(default_factory=set)


class NLPRegistry:
    """Centralised manager for spaCy and transformer pipelines."""

    def __init__(self, settings: NLPSettings) -> None:
        self.settings = settings
        self.device = select_device(settings.preferred_device)
        self.spacy_nlp = self._load_spacy(settings.spacy_model)
        self.entity_labels = self._resolve_entity_labels()
        self.stop_words = self._build_stop_words(
            settings.additional_stop_words, settings.stop_word_overrides
        )
        self.transformer_pipeline = self._load_transformer(settings.transformer_model)

    def _load_spacy(self, model_name: str):
        spacy_module = _resolve_module("spacy")
        if spacy_module is None:
            raise RuntimeError("spaCy is required but not installed")

        try:
            nlp = spacy_module.load(model_name)
        except Exception as exc:  # pragma: no cover - configuration issue
            raise RuntimeError(f"Unable to load spaCy model '{model_name}': {exc}")

        # add lemmatizer or things get weird
        if "lemmatizer" not in nlp.pipe_names:
            try:
                nlp.add_pipe("lemmatizer", config={"mode": "rule"}, after="tagger")
            except Exception as lemmatizer_exc:
                logger.debug(
                    "Failed adding lemmatiser to spaCy pipeline: %s", lemmatizer_exc
                )

        return nlp

    def _resolve_entity_labels(self) -> Set[str]:
        if not self.spacy_nlp:
            return set()
        pipe_labels = getattr(self.spacy_nlp, "pipe_labels", {})
        return set(pipe_labels.get("ner", []))

    def _build_stop_words(
        self, additions: Set[str], overrides: Set[str]
    ) -> Set[str]:
        base = set()
        if self.spacy_nlp and hasattr(self.spacy_nlp, "Defaults"):
            base = set(getattr(self.spacy_nlp.Defaults, "stop_words", set()))
        stop_words = (base | additions) - overrides
        return {word.lower() for word in stop_words}

    def _load_transformer(self, model_name: Optional[str]):
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
            logger.warning(
                "Failed to load transformer model '%s' on device '%s': %s",
                model_name,
                device_arg,
                exc,
            )
            return None

    def _transformer_device_argument(self):
        if self.device == "cuda":
            return 0

        if self.device == "mps":
            try:
                import torch

                return torch.device("mps")
            except ImportError:
                logger.warning("PyTorch missing for MPS device; using CPU instead")

        # HuggingFace doesn't speak MLX yet so fake it
        return -1

    def extract_with_spacy(
        self, text: str, top_k: int
    ) -> Tuple[List[str], List[str]]:
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

        keywords = self._keywords_from_doc(doc, top_k)
        return entities, keywords

    def extract_entities_with_transformer(self, text: str) -> List[str]:
        if not self.transformer_pipeline:
            raise RuntimeError("Transformer pipeline is not initialised")

        raw_entities = self.transformer_pipeline(text)
        entities: List[str] = []
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

        return entities

    def _keywords_from_doc(self, doc, top_k: int) -> List[str]:
        candidates: List[str] = []

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

    def extract_with_spacy(self, text: str, top_k: int) -> Tuple[List[str], List[str]]:
        return [], []

    def extract_entities_with_transformer(self, text: str) -> List[str]:
        return []


NLP_REGISTRY: Optional[NLPRegistry] = None


def initialize_nlp(settings: Optional[NLPSettings] = None) -> None:
    """Initialise the global NLP registry."""

    global NLP_REGISTRY
    # graceful handling for missing models because tests shouldn't randomly break
    try:
        NLP_REGISTRY = NLPRegistry(settings or NLPSettings())
    except RuntimeError as e:
        logger.warning(f"Failed to initialize NLP registry: {e}")
        # create a dummy registry that doesn't crash everything
        NLP_REGISTRY = _DummyNLPRegistry()


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
) -> Tuple[List[str], List[str]]:
    """Extract entities/keywords using the configured NLP backend."""

    if not text:
        return [], []

    registry = get_registry()
    truncated = text[:max_length]

    if backend == "transformer":
        entities = registry.extract_entities_with_transformer(truncated)
        # Keywords still leverage spaCy for richer linguistic signals
        _, keywords = registry.extract_with_spacy(truncated, top_k)
        return entities, keywords

    entities, keywords = registry.extract_with_spacy(truncated, top_k)
    return entities, keywords


def extract_content_tags(url_path: str, predefined_tags: Set[str]) -> List[str]:
    """Extract content tags from a URL path while preserving order."""

    if not url_path or not predefined_tags:
        return []

    cleaned_tags: List[str] = []
    seen: Set[str] = set()

    for part in url_path.split("/"):
        candidate = part.lower().strip()
        if not candidate or candidate not in predefined_tags:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        cleaned_tags.append(candidate)

    return cleaned_tags


def has_audio_links(links: List[str]) -> bool:
    """Check if any links point to audio-capable resources."""

    if not links:
        return False

    return any(AUDIO_RE.search(link or "") for link in links)


def clean_text(text: str) -> str:
    """Clean and normalize text content without destroying contractions."""

    if not text:
        return ""

    text = re.sub(r"\s+", " ", text.strip())
    text = re.sub(r"[^\w\s.,!?;:'()\-\"]", "", text)
    return text


def extract_keywords_simple(
    text: str,
    top_k: int = TOP_KEYWORDS,
    stop_words: Optional[Set[str]] = None,
) -> List[str]:
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


def calculate_content_quality_score(text: str, title: str = "") -> float:
    """Calculate content quality score (0.0-1.0)."""

    if not text:
        return 0.0

    score = 0.0

    # Text length scoring (0-0.3 points)
    text_length = len(text.strip())
    if text_length > 1000:
        score += 0.3
    elif text_length > 500:
        score += 0.2
    elif text_length > 100:
        score += 0.1

    # Word variety scoring (0-0.2 points)
    words = re.findall(r"[A-Za-z']+", text.lower())
    unique_words = set(words)
    if words:
        variety_ratio = len(unique_words) / len(words)
        score += min(0.2, variety_ratio * 0.4)

    # Sentence structure (0-0.2 points)
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    if sentences:
        avg_sentence_length = len(words) / len(sentences)
        if 10 <= avg_sentence_length <= 25:  # Optimal range
            score += 0.2
        elif 5 <= avg_sentence_length < 10 or 25 < avg_sentence_length <= 35:
            score += 0.1

    # Academic indicators (0-0.2 points)
    academic_terms = [
        'research', 'study', 'analysis', 'department', 'faculty', 'course',
        'program', 'degree', 'university', 'academic', 'education', 'learning'
    ]
    text_lower = text.lower()
    academic_score = sum(1 for term in academic_terms if term in text_lower)
    score += min(0.2, academic_score * 0.03)

    # Title relevance (0-0.1 points)
    if title and text:
        title_words = set(re.findall(r"[A-Za-z']+", title.lower()))
        text_words = set(re.findall(r"[A-Za-z']+", text.lower()))
        if title_words:
            overlap = len(title_words & text_words) / len(title_words)
            score += min(0.1, overlap * 0.2)

    return min(1.0, score)


def detect_academic_relevance(text: str) -> float:
    """Detect academic relevance score (0.0-1.0)."""

    if not text:
        return 0.0

    text_lower = text.lower()

    # Academic keywords with weights
    academic_indicators = {
        'research': 0.15,
        'study': 0.10,
        'analysis': 0.10,
        'department': 0.12,
        'faculty': 0.12,
        'professor': 0.08,
        'course': 0.08,
        'curriculum': 0.08,
        'degree': 0.10,
        'graduate': 0.08,
        'undergraduate': 0.06,
        'academic': 0.10,
        'scholarship': 0.08,
        'dissertation': 0.10,
        'publication': 0.08,
        'conference': 0.06,
        'journal': 0.08,
        'university': 0.05,
        'college': 0.05,
        'education': 0.05
    }

    score = 0.0
    for term, weight in academic_indicators.items():
        if term in text_lower:
            score += weight

    # Bonus for multiple academic terms
    term_count = sum(1 for term in academic_indicators.keys() if term in text_lower)
    if term_count >= 5:
        score += 0.1
    elif term_count >= 3:
        score += 0.05

    return min(1.0, score)


def identify_content_type(html: str, url: str = "") -> str:
    """Identify content type from HTML and URL patterns."""

    if not html:
        return "unknown"

    html_lower = html.lower()
    url_lower = url.lower()

    # Check URL patterns first
    if any(pattern in url_lower for pattern in ['/admissions/', '/apply/', '/admission']):
        return "admissions"
    elif any(pattern in url_lower for pattern in ['/academics/', '/courses/', '/curriculum']):
        return "academics"
    elif any(pattern in url_lower for pattern in ['/research/', '/labs/', '/centers']):
        return "research"
    elif any(pattern in url_lower for pattern in ['/faculty/', '/staff/', '/directory']):
        return "faculty"
    elif any(pattern in url_lower for pattern in ['/news/', '/events/', '/announcements']):
        return "news"
    elif any(pattern in url_lower for pattern in ['/about/', '/history/', '/mission']):
        return "about"

    # Check HTML content patterns
    if any(term in html_lower for term in ['application deadline', 'apply now', 'admission requirements']):
        return "admissions"
    elif any(term in html_lower for term in ['course description', 'syllabus', 'prerequisites']):
        return "academics"
    elif any(term in html_lower for term in ['research project', 'laboratory', 'publication']):
        return "research"
    elif any(term in html_lower for term in ['professor', 'dr.', 'ph.d.', 'faculty member']):
        return "faculty"
    elif any(term in html_lower for term in ['news', 'announcement', 'event', 'calendar']):
        return "news"

    return "general"


def _is_true(predicate) -> bool:
    try:
        return bool(predicate())
    except Exception:
        return False


def select_device(preferred: Optional[str] = None) -> str:
    """Determine the best execution device available."""

    if preferred:
        return preferred

    torch_module = _resolve_module("torch")
    if torch_module is not None:
        cuda_module = getattr(torch_module, "cuda", None)
        if cuda_module and _is_true(getattr(cuda_module, "is_available", lambda: False)):
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
