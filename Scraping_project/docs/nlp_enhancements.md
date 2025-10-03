# NLP Enhancements Documentation

## Overview

This document describes the comprehensive NLP enhancements implemented in the UConn Web Scraping Pipeline. These improvements significantly increase the accuracy and relevance of extracted entities, keywords, and content categorization.

---

## Table of Contents

- [What's New](#whats-new)
- [Taxonomy-Based Classification](#taxonomy-based-classification)
- [UConn-Specific Glossary](#uconn-specific-glossary)
- [Smarter Text Extraction](#smarter-text-extraction)
- [Entity Post-Processing](#entity-post-processing)
- [Upgrading NLP Models](#upgrading-nlp-models)
- [Configuration Guide](#configuration-guide)
- [Usage Examples](#usage-examples)

---

## What's New

### 1. **Smarter Text Extraction**
The pipeline now intelligently removes non-content elements before NLP processing:
- Navigation menus
- Headers and footers
- Sidebar content
- Elements with `role="navigation"`, `role="menu"`, etc.
- Elements with classes containing "nav", "menu", "footer", "sidebar"

**Result**: Only main page content is processed, dramatically improving keyword and entity quality.

### 2. **Entity Post-Processing**
Extracted entities are now filtered to remove:
- Entities longer than 6 words (likely extraction errors)
- Entities containing newline characters
- Duplicate entities (case-insensitive)
- Entities without any letters (numbers/punctuation only)
- Invalid or nonsensical text fragments

**Result**: Cleaner, more meaningful entity lists.

### 3. **Taxonomy-Based Classification**
Content is automatically categorized using a comprehensive 15-category taxonomy covering:
- Academics (6 subcategories)
- Research (5 subcategories)
- Student Services (8 subcategories)
- Athletics (3 subcategories)
- Healthcare (6 subcategories)
- Administration (6 subcategories)
- Libraries (2 subcategories)
- Admissions (4 subcategories)
- Schools & Colleges (9 subcategories)
- Campus Life (4 subcategories)
- Alumni Relations (2 subcategories)
- Community & Outreach (3 subcategories)
- News & Media (2 subcategories)
- Policies & Compliance (3 subcategories)
- Technology & Platforms (3 subcategories)

**Total**: 66 subcategories with associated keywords

**Result**: Pages automatically tagged with relevant categories.

### 4. **UConn-Specific Glossary**
Custom glossary ensures UConn-specific terms are always recognized as keywords:
- **Platforms**: HuskyCT, StudentAdmin, PeopleSoft, NetID
- **Buildings**: Gampel Pavilion, Homer Babbidge Library, Storrs Hall, etc.
- **Schools**: CLAS, SoE, SoB, Neag School, CAHNR
- **Departments**: CETL, DOS, OUR, CSD, UCPD
- **Programs**: Honors Program, FYE, Study Abroad
- **Athletics**: UConn Huskies, Jonathan the Husky
- **Events**: Spring Weekend, Homecoming
- **Administrative**: Board of Trustees, USG, GSS

**Total**: 100+ UConn-specific terms with aliases

**Result**: Important institutional terms never missed in keyword extraction.

### 5. **Improved NLP Model Support**
Now supports multiple spaCy models and transformer-based models:
- **en_core_web_sm**: Fast, 13MB (default for development)
- **en_core_web_md**: Better accuracy, 40MB
- **en_core_web_lg**: Best accuracy, 560MB
- **en_core_web_trf**: Transformer-based, 438MB, highest accuracy
- **Transformer NER**: Optional BERT-based entity recognition
- **Zero-shot classification**: Advanced categorization

---

## Taxonomy-Based Classification

### How It Works

The taxonomy-based classification system matches content against predefined categories using keyword matching.

**Location**: `data/config/taxonomy.json`

### Structure

```json
{
  "taxonomy_version": "1.0",
  "categories": [
    {
      "id": "academics",
      "label": "Academics",
      "description": "Academic programs, courses, and educational content",
      "subcategories": [
        {
          "id": "academics.undergraduate",
          "label": "Undergraduate Programs",
          "keywords": ["undergraduate", "bachelor", "bachelor's degree", ...]
        }
      ]
    }
  ]
}
```

### Category Hierarchy

```
Academics
├── Undergraduate Programs
├── Graduate Programs
├── Professional Programs
├── Online & Distance Learning
├── Continuing Education
└── International Programs

Research
├── Research Labs & Centers
├── Publications & Papers
├── Grants & Funding
├── Innovation & Technology Transfer
└── Research Collaboration

Student Services
├── Academic Advising
├── Housing & Residential Life
├── Dining Services
├── Student Health Services
├── Career Services
├── Disability Services
├── Financial Aid
└── Student Organizations & Clubs

... (12 more main categories)
```

### Usage in Code

```python
from src.common.nlp import load_taxonomy, classify_with_taxonomy

# Load taxonomy
taxonomy = load_taxonomy()

# Classify text
results = classify_with_taxonomy(text_content, taxonomy, top_k=5)

# Results format:
# [
#   {
#     "category_id": "healthcare",
#     "category_label": "Healthcare",
#     "score": 12.0,
#     "matched_keywords": ["hospital", "patient care", "clinical services", ...]
#   },
#   ...
# ]
```

### Extending the Taxonomy

To add new categories:

1. Edit `data/config/taxonomy.json`
2. Add category with unique `id` and `label`
3. Add subcategories with relevant `keywords`
4. No code changes required - automatically loaded

---

## UConn-Specific Glossary

### How It Works

The glossary ensures institutional terminology is always recognized, even if not picked up by standard NLP.

**Location**: `data/config/uconn_glossary.json`

### Structure

```json
{
  "glossary_version": "1.0",
  "terms": {
    "platforms_systems": [
      {
        "term": "HuskyCT",
        "aliases": ["Husky CT", "Blackboard"],
        "category": "Learning Management System",
        "description": "UConn's learning management system"
      }
    ],
    "buildings_locations": [...],
    "schools_colleges": [...],
    ...
  }
}
```

### Term Categories

1. **Platforms & Systems**: HuskyCT, StudentAdmin, PeopleSoft, NetID
2. **Buildings & Locations**: Campus buildings, regional campuses
3. **Schools & Colleges**: All academic units with acronyms
4. **Departments & Centers**: Support units and research centers
5. **Programs & Initiatives**: Special programs and learning communities
6. **Athletics & Teams**: Sports teams and mascots
7. **Events & Traditions**: Annual campus events
8. **Administrative Terms**: Governance bodies
9. **Course Codes**: Common course identifiers
10. **Acronyms**: FAFSA, GPA, NCAA, STEM

### Usage in Code

```python
from src.common.nlp import load_glossary, extract_glossary_terms

# Load glossary
glossary = load_glossary()

# Extract terms from text
glossary_terms = extract_glossary_terms(text_content, glossary)

# Returns: ["HuskyCT", "Gampel Pavilion", "CLAS", ...]
```

### Extending the Glossary

To add new terms:

1. Edit `data/config/uconn_glossary.json`
2. Add term to appropriate category
3. Include aliases for variations
4. No code changes required

---

## Smarter Text Extraction

### Problem Solved

Previously, all text from the page was extracted, including:
- Navigation menus ("Home | About | Contact")
- Footer links ("Privacy Policy | Terms of Service")
- Sidebar content
- Breadcrumb navigation

This polluted the NLP results with irrelevant keywords.

### Solution

XPath-based filtering excludes non-content elements:

```xpath
//body//text()[
    normalize-space()
    and not(ancestor::script)
    and not(ancestor::style)
    and not(ancestor::nav)
    and not(ancestor::footer)
    and not(ancestor::header)
    and not(ancestor::*[@role="navigation"])
    and not(ancestor::*[@role="menu"])
    and not(ancestor::*[@role="menubar"])
    and not(ancestor::*[@role="banner"])
    and not(ancestor::*[@role="contentinfo"])
    and not(ancestor::*[contains(@class, "nav")])
    and not(ancestor::*[contains(@class, "menu")])
    and not(ancestor::*[contains(@class, "footer")])
    and not(ancestor::*[contains(@class, "header")])
    and not(ancestor::*[contains(@class, "sidebar")])
]
```

### Before vs. After

**Before (with navigation/footer)**:
```
Keywords: ["home", "about", "contact", "privacy", "policy", "terms", "login", "search", ...]
```

**After (content only)**:
```
Keywords: ["research", "graduate", "program", "faculty", "laboratory", "publication", ...]
```

---

## Entity Post-Processing

### Filtering Rules

The `filter_entities()` function applies these rules:

1. **Length check**: Skip entities longer than 6 words
   - ❌ "Skip Navigation Give Search UConn Health A-Z Patient Care"
   - ✅ "UConn Health"

2. **Newline check**: Skip entities containing `\n` or `\r`
   - ❌ "Patient Care\nResearch"
   - ✅ "Patient Care"

3. **Letter check**: Must contain at least one letter
   - ❌ "2025"
   - ❌ "---"
   - ✅ "UConn 2025"

4. **Numeric/punctuation check**: Can't be only numbers/punctuation
   - ❌ "123.45"
   - ❌ "!!!"
   - ✅ "Section 123"

5. **Deduplication**: Case-insensitive duplicate removal
   - Input: ["UConn", "uconn", "UCONN"]
   - Output: ["UConn"]

### Code Location

**File**: `src/common/nlp.py`

**Function**: `filter_entities(entities: list[str]) -> list[str]`

---

## Upgrading NLP Models

### Available spaCy Models

| Model | Size | Speed | Accuracy | Use Case |
|-------|------|-------|----------|----------|
| `en_core_web_sm` | 13 MB | Fast | Good | Development, testing |
| `en_core_web_md` | 40 MB | Medium | Better | Production (balanced) |
| `en_core_web_lg` | 560 MB | Slow | Best | High accuracy required |
| `en_core_web_trf` | 438 MB | Slowest | Highest | Maximum accuracy |

### Installation

```bash
# Small model (default)
python -m spacy download en_core_web_sm

# Medium model (recommended for production)
python -m spacy download en_core_web_md

# Large model (best accuracy)
python -m spacy download en_core_web_lg

# Transformer model (highest accuracy)
python -m spacy download en_core_web_trf
```

### Transformer-Based NER (Optional)

For even better entity recognition, enable transformer-based models:

```yaml
# config/development.yml
nlp:
  use_transformers: true
  transformer_ner_model: "dslim/bert-base-NER"
  device: "auto"  # Will use GPU if available
```

**Requirements**:
```bash
pip install transformers torch
```

**Supported devices**:
- CUDA (NVIDIA GPU)
- MPS (Apple Silicon)
- CPU (fallback)

---

## Configuration Guide

### Basic Configuration

**File**: `config/development.yml`

```yaml
nlp:
  # Choose spaCy model
  spacy_model: "en_core_web_sm"  # or en_core_web_lg, en_core_web_trf

  # Optional: Enable transformer models
  use_transformers: false
  transformer_ner_model: "dslim/bert-base-NER"
  summarizer_model: "sshleifer/distilbart-cnn-12-6"
  zero_shot_model: "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"

  # Device selection
  device: "auto"  # or "cuda", "mps", "cpu"

  # Text processing limits
  max_text_length: 20000
  top_keywords: 15

  # Summary settings (for transformers)
  summary_max_length: 150
  summary_min_length: 30
```

### Production Configuration

**File**: `config/production.yml`

```yaml
nlp:
  # Use larger model for better accuracy
  spacy_model: "en_core_web_lg"

  # Enable transformers if GPU available
  use_transformers: true
  device: "cuda"  # Assumes GPU server

  # Larger limits for production
  max_text_length: 50000
  top_keywords: 20
```

### Environment Variables

Override configuration via environment variables:

```bash
# Override spaCy model
export NLP_SPACY_MODEL=en_core_web_lg

# Enable transformers
export NLP_USE_TRANSFORMERS=true

# Force CPU (no GPU)
export NLP_DEVICE=cpu

# Run pipeline
python main.py --env production --stage 3
```

---

## Usage Examples

### Example 1: Basic Enrichment with Default Settings

```bash
# Use default small spaCy model
python main.py --env development --stage 3
```

**Output**:
```json
{
  "url": "https://health.uconn.edu/",
  "title": "Home | UConn Health",
  "entities": ["UConn Health", "Connecticut", "John Dempsey Hospital"],
  "keywords": ["HuskyCT", "uconn", "health", "patient", "care", "research"],
  "content_tags": ["Healthcare", "Medical Education"]
}
```

### Example 2: High-Accuracy Mode with Large Model

```yaml
# config/development.yml
nlp:
  spacy_model: "en_core_web_lg"
```

```bash
python -m spacy download en_core_web_lg
python main.py --env development --stage 3
```

**Result**: More accurate entities and better keyword extraction.

### Example 3: Transformer-Based NER

```yaml
# config/development.yml
nlp:
  use_transformers: true
  transformer_ner_model: "dslim/bert-base-NER"
  device: "cuda"  # If you have GPU
```

```bash
pip install transformers torch
python main.py --env development --stage 3
```

**Result**: State-of-the-art entity recognition using BERT.

### Example 4: Custom Taxonomy Extension

Add new category to `data/config/taxonomy.json`:

```json
{
  "id": "sustainability",
  "label": "Sustainability Initiatives",
  "subcategories": [
    {
      "id": "sustainability.energy",
      "label": "Energy & Climate",
      "keywords": ["solar", "renewable", "carbon neutral", "climate action"]
    },
    {
      "id": "sustainability.waste",
      "label": "Waste Reduction",
      "keywords": ["recycling", "composting", "zero waste", "circular economy"]
    }
  ]
}
```

No code changes needed - automatically used in next run.

### Example 5: Adding Glossary Terms

Add new term to `data/config/uconn_glossary.json`:

```json
{
  "term": "UConn Foundation",
  "aliases": ["UCFF", "Foundation"],
  "category": "Administrative",
  "description": "University fundraising organization"
}
```

Next enrichment run will recognize these terms.

---

## Performance Considerations

### Model Comparison

| Configuration | Speed (pages/min) | Accuracy | Memory |
|---------------|-------------------|----------|--------|
| sm + no transformers | ~500 | Good | 500 MB |
| lg + no transformers | ~200 | Better | 2 GB |
| trf + no transformers | ~100 | Best | 3 GB |
| sm + transformers | ~50 | Excellent | 4 GB |
| lg + transformers | ~30 | Outstanding | 6 GB |

### Recommendations

**Development/Testing**:
- Use `en_core_web_sm` with `use_transformers: false`
- Fast iteration, acceptable accuracy

**Production (CPU server)**:
- Use `en_core_web_lg` with `use_transformers: false`
- Good balance of speed and accuracy

**Production (GPU server)**:
- Use `en_core_web_lg` with `use_transformers: true`
- Maximum accuracy, requires GPU

---

## Troubleshooting

### Issue: Entities still contain garbage

**Solution**: Check that entity filtering is enabled in `nlp.py`:
```python
entities = filter_entities(entities)
```

### Issue: Glossary terms not recognized

**Solution**: Verify glossary file path:
```bash
ls data/config/uconn_glossary.json
```

Check logs for loading errors.

### Issue: Taxonomy categories empty

**Solution**: Ensure text extraction is working:
```python
# Check text_content is not empty
print(len(text_content))  # Should be > 100 for most pages
```

### Issue: Transformer models fail to load

**Solution**: Install dependencies:
```bash
pip install transformers torch
```

Check device compatibility:
```bash
python -c "import torch; print(torch.cuda.is_available())"
```

### Issue: Out of memory with transformers

**Solution**: Reduce batch size or switch to smaller model:
```yaml
nlp:
  spacy_model: "en_core_web_sm"  # Smaller model
  use_transformers: false  # Disable transformers
```

---

## Next Steps

1. **Monitor Results**: Check `data/processed/stage03/enriched_content.jsonl` for improved data quality

2. **Tune Configuration**: Adjust model sizes based on accuracy/speed requirements

3. **Extend Taxonomy**: Add university-specific categories as needed

4. **Expand Glossary**: Add new terms discovered during crawling

5. **Enable GPU**: For production, use GPU server with transformer models for best results

---

## Related Documentation

- [Project Internals](project_internals.md) - Overall pipeline architecture
- [Sprint Backlog](../SPRINT_BACKLOG.md) - Feature roadmap
- [Configuration Guide](project_internals.md#configuration-management) - Full config options
