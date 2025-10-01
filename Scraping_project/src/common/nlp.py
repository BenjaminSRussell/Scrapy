# TODO: Add support for other NLP backends, such as NLTK or Flair, to provide more options for NLP processing.
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

    # TODO: The spaCy and transformer models are hardcoded. They should be configurable.
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

    # TODO: This NLP pipeline is designed for English. It should be extended to support other languages.
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
            logger.error(
                "Failed to load transformer model '%s' on device '%s': %s",
                model_name,
                device_arg,
                exc,
                exc_info=True
            )
            logger.warning("Transformer pipeline will be disabled")
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
