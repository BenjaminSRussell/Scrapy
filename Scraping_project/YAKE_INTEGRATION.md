# YAKE Keyword Extraction Integration

## Overview

The scraping pipeline now uses **YAKE (Yet Another Keyword Extractor)** for advanced keyword extraction, replacing the basic frequency-based approach.

## What is YAKE?

YAKE is a statistical keyword extraction method that:
- Uses local text features to identify important keywords
- Doesn't require training data or dictionaries
- Extracts both unigrams and bigrams
- Performs intelligent deduplication
- Provides better context-aware keyword selection

## Benefits

### Before (Basic Frequency):
```
Keywords: ['research', 'computer', 'science', 'university', 'connecticut', 'offer', 'excellent', 'program', 'engineering']
```

### After (YAKE):
```
Keywords: ['computer science', 'machine learning', 'artificial intelligence', 'research programs', 'excellent research']
```

## Configuration

YAKE is configured with optimal parameters for academic content:
- **Max n-gram size**: 2 (unigrams and bigrams)
- **Deduplication threshold**: 0.7 (aggressive deduplication)
- **Window size**: 1 (tighter context for better precision)
- **Language**: English

## Usage

YAKE is automatically enabled by default in the NLP pipeline. To disable it:

```python
from src.common.nlp import NLPSettings, initialize_nlp

# Disable YAKE and fall back to spaCy frequency-based extraction
settings = NLPSettings(use_yake_keywords=False)
initialize_nlp(settings)
```

## Dependencies

YAKE has been added to `requirements.txt`:
```
yake>=0.6.0  # YAKE keyword extraction
```

## Integration Points

YAKE is integrated in:
1. **`src/common/nlp.py`**: Core NLP registry with YAKE support
2. **`src/stage3/enrichment_spider.py`**: Uses YAKE for keyword extraction during enrichment
3. All NLP-based pipelines automatically benefit from YAKE extraction

## Technical Details

- **Fallback behavior**: If YAKE is unavailable, the system gracefully falls back to spaCy frequency-based extraction
- **Stop word filtering**: YAKE results are filtered against spaCy stop words
- **Length filtering**: Keywords shorter than 3 characters are excluded
- **Case normalization**: All keywords are lowercased for consistency
