import re
from collections import Counter
from typing import List, Tuple, Set, Optional

# Global variables for lazy loading
NLP = None
ENTITY_LABELS = None

MAX_TEXT_LENGTH = 20_000
TOP_KEYWORDS = 15

# Audio file pattern matching
AUDIO_RE = re.compile(r"\.(mp3|wav|ogg|flac)(\?.*)?$", re.I)


def load_nlp_model() -> bool:
    """Load spaCy model lazily"""
    global NLP, ENTITY_LABELS

    if NLP is None:
        try:
            import spacy
            NLP = spacy.load("en_core_web_sm", disable=["lemmatizer", "parser"])
            ENTITY_LABELS = set(NLP.pipe_labels["ner"])
            return True
        except (ImportError, OSError):
            # spaCy or model not available
            NLP = None
            ENTITY_LABELS = set()
            return False

    return True


def extract_entities_and_keywords(text: str, max_length: int = MAX_TEXT_LENGTH, top_k: int = TOP_KEYWORDS) -> Tuple[List[str], List[str]]:
    """Extract entities and keywords from text using spaCy"""
    if not text or not load_nlp_model():
        return [], []

    try:
        # Truncate text if too long
        if len(text) > max_length:
            text = text[:max_length]

        doc = NLP(text)

        # Extract named entities
        entities = {e.text.strip() for e in doc.ents if e.label_ in ENTITY_LABELS}

        # Extract keywords (lemmatized tokens, excluding stop words)
        lemmas = [
            t.lemma_.lower()
            for t in doc
            if t.is_alpha and not t.is_stop and len(t.lemma_) > 2
        ]

        # Get most common keywords
        keywords = [word for word, _ in Counter(lemmas).most_common(top_k)]

        return sorted(entities), keywords

    except Exception as e:
        # Log error but don't fail the entire process
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"NLP processing failed: {e}")
        return [], []


def extract_content_tags(url_path: str, predefined_tags: Set[str]) -> List[str]:
    """Extract content tags from URL path based on predefined tag set"""
    if not url_path or not predefined_tags:
        return []

    # Split path and clean components
    path_parts = [
        part.lower().strip()
        for part in url_path.split('/')
        if part and part.lower().strip()
    ]

    # Find matches with predefined tags
    tags = [part for part in path_parts if part in predefined_tags]

    return sorted(set(tags))  # Remove duplicates and sort


def has_audio_links(links: List[str]) -> bool:
    """Check if any links point to audio files"""
    if not links:
        return False

    return any(AUDIO_RE.search(link) for link in links)


def clean_text(text: str) -> str:
    """Clean and normalize text content"""
    if not text:
        return ""

    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text.strip())

    # Remove special characters but keep basic punctuation
    text = re.sub(r'[^\w\s.,!?;:()\-"]', '', text)

    return text


def extract_keywords_simple(text: str, top_k: int = TOP_KEYWORDS) -> List[str]:
    """Extract keywords using simple word frequency (fallback when spaCy unavailable)"""
    if not text:
        return []

    # Simple tokenization
    words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())

    # Basic stop words
    stop_words = {
        'the', 'and', 'are', 'for', 'with', 'this', 'that', 'from', 'they',
        'have', 'has', 'will', 'can', 'been', 'was', 'were', 'you', 'your',
        'our', 'all', 'any', 'may', 'also', 'more', 'than', 'not', 'but'
    }

    # Filter stop words and count frequency
    filtered_words = [word for word in words if word not in stop_words]
    word_counts = Counter(filtered_words)

    return [word for word, _ in word_counts.most_common(top_k)]


def get_text_stats(text: str) -> dict:
    """Get basic statistics about text content"""
    if not text:
        return {
            'word_count': 0,
            'char_count': 0,
            'sentence_count': 0,
            'avg_word_length': 0
        }

    words = text.split()
    sentences = re.split(r'[.!?]+', text)

    return {
        'word_count': len(words),
        'char_count': len(text),
        'sentence_count': len([s for s in sentences if s.strip()]),
        'avg_word_length': sum(len(word) for word in words) / len(words) if words else 0
    }