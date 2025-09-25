# UConn Web Scraping Pipeline

A multi-stage web scraping pipeline for discovering, validating, and enriching UConn website content.

## Quick Start

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   python -m spacy download en_core_web_sm
   ```

2. **Prepare seed data**:
   - Place your seed URLs in `data/raw/uconn_urls.csv` (one URL per line)

3. **Run the pipeline**:
   ```bash
   # Run Stage 1 discovery only
   python main.py --stage=1 --env=development

   # Run all stages
   python main.py --stage=all --env=development
   ```

## Architecture

The pipeline uses a staged architecture with async orchestration:

1. **Stage 1: Discovery** - Scrapy spider crawls seed URLs and discovers new links
2. **Stage 2: Validation** - Async HTTP client validates URLs with HEAD/GET requests
3. **Stage 3: Enrichment** - Scrapy spider extracts content and metadata from valid URLs

Each stage operates independently, reading from the previous stage's output files.

## Project Structure

```
├── main.py                          # Single entry point (delegates to orchestrator)
├── requirements.txt                 # Python dependencies
├── README.md                        # Documentation
├── config/                          # Environment configurations
│   ├── development.yml             # Dev settings (lower concurrency, more logging)
│   └── production.yml              # Prod settings (higher concurrency, optimized)
├── src/                            # All source code
│   ├── orchestrator/               # Pipeline orchestration
│   │   ├── main.py                # Main orchestrator (sets up logging, loads config)
│   │   ├── config.py              # YAML config loader with env overrides
│   │   └── pipeline.py            # Batch queue management and backpressure
│   ├── stage1/                    # Discovery stage
│   │   ├── discovery_spider.py   # Scrapy spider returning DiscoveryItem
│   │   └── discovery_pipeline.py # JSONL writer pipeline
│   ├── stage2/                    # Validation stage
│   │   └── validator.py          # Async HTTP client for URL validation
│   ├── stage3/                    # Enrichment stage
│   │   └── enrichment_spider.py  # Content extraction and NLP processing
│   └── common/                    # Shared utilities
│       ├── logging.py            # Centralized logging setup
│       ├── urls.py               # URL canonicalization and SHA-1 hashing
│       ├── nlp.py                # spaCy NLP helpers (entities, keywords)
│       ├── schemas.py            # Data schemas (DiscoveryItem, ValidationResult, etc.)
│       └── storage.py            # JSONL and SQLite storage abstractions
├── data/                          # Data storage (created automatically)
│   ├── raw/                      # Input data (place uconn_urls.csv here)
│   ├── processed/                # Stage outputs
│   │   ├── stage01/             # Discovery results (new_urls.jsonl)
│   │   ├── stage02/             # Validation results (validated_urls.jsonl)
│   │   └── stage03/             # Enrichment results (enriched_data.jsonl)
│   ├── catalog/                 # URL catalogs and indices
│   ├── cache/                   # SQLite caches and temporary data
│   ├── exports/                 # Final processed exports
│   └── logs/                    # Pipeline execution logs
└── tests/                         # Test framework
    ├── fixtures/                 # Test data and mock responses
    ├── pipelines/               # Pipeline tests
    ├── spiders/                 # Spider tests
    └── utils/                   # Utility tests
```

## Command Line Usage

### Basic Commands

```bash
# Run specific stage with development config
python main.py --stage=1 --env=development

# Run all stages with production config
python main.py --stage=all --env=production

# Show configuration and exit
python main.py --config-only --env=development

# Run with debug logging
python main.py --stage=1 --log-level=DEBUG
```

### Command Line Options

```bash
python main.py --help

Options:
  --env {development,production}       Environment configuration (default: development)
  --stage {1,2,3,all}                 Stage(s) to run (default: all)
  --config-only                       Show configuration and exit
  --log-level {DEBUG,INFO,WARNING,ERROR}  Logging level (default: INFO)
```

## Configuration

### YAML Configuration Files

Configuration is driven by YAML files in the `config/` directory:

- **`development.yml`** - Development settings (lower concurrency, more logging, safer defaults)
- **`production.yml`** - Production settings (higher concurrency, optimized for performance)

### Environment Variable Overrides

You can override specific settings using environment variables:

```bash
# Override Scrapy concurrency
export SCRAPY_CONCURRENT_REQUESTS=64
export SCRAPY_DOWNLOAD_DELAY=0.05

# Override stage-specific settings
export STAGE1_MAX_DEPTH=5
export STAGE1_BATCH_SIZE=2000

# Run with overrides
python main.py --stage=1
```

Available overrides:
- `SCRAPY_CONCURRENT_REQUESTS` - Concurrent request limit
- `SCRAPY_DOWNLOAD_DELAY` - Delay between requests
- `STAGE1_MAX_DEPTH` - Crawling depth limit
- `STAGE1_BATCH_SIZE` - Batch processing size

## Pipeline Stages

### Stage 1: Discovery

**Purpose**: Crawl seed URLs and discover new links within the uconn.edu domain.

- **Input**: Seed URLs from `data/raw/uconn_urls.csv`
- **Process**:
  - Scrapy spider loads seed URLs
  - Crawls pages up to configured depth limit
  - Extracts links using LinkExtractor
  - Canonicalizes URLs and generates SHA-1 hashes
  - Deduplicates based on URL hash
- **Output**: `data/processed/stage01/new_urls.jsonl`
- **Key Features**:
  - Configurable depth limiting via YAML/env vars
  - URL canonicalization with w3lib
  - SHA-1 hash-based deduplication
  - Filtered file extensions (no images, PDFs, etc.)
  - Respects domain restrictions

**Output Format**:
```json
{
  "source_url": "https://uconn.edu/about/",
  "discovered_url": "https://uconn.edu/admissions/",
  "first_seen": "2025-01-15T10:30:00",
  "url_hash": "a1b2c3d4e5f6...",
  "discovery_depth": 2
}
```

### Stage 2: Validation

**Purpose**: Validate discovered URLs to ensure they're accessible and contain HTML content.

- **Input**: URLs from Stage 1 output (`data/processed/stage01/new_urls.jsonl`)
- **Process**:
  - Async HTTP client processes URLs in batches
  - Performs HEAD requests first (faster)
  - Falls back to GET requests if needed
  - Validates status codes and content types
  - Handles timeouts and connection errors
- **Output**: `data/processed/stage02/validated_urls.jsonl`
- **Key Features**:
  - Configurable worker count for concurrent processing
  - Timeout handling and error recovery
  - Content-type filtering (HTML only)
  - Response time measurement
  - Connection pooling with aiohttp

**Output Format**:
```json
{
  "url": "https://uconn.edu/admissions/",
  "url_hash": "a1b2c3d4e5f6...",
  "status_code": 200,
  "content_type": "text/html; charset=utf-8",
  "content_length": 15420,
  "response_time": 0.245,
  "is_valid": true,
  "error_message": null,
  "validated_at": "2025-01-15T10:35:00"
}
```

### Stage 3: Enrichment

**Purpose**: Extract content and metadata from validated URLs using NLP processing.

- **Input**: Valid URLs from Stage 2 output (`data/processed/stage02/validated_urls.jsonl`)
- **Process**:
  - Scrapy spider fetches page content
  - Extracts title and body text
  - Performs NLP analysis with spaCy
  - Extracts named entities and keywords
  - Classifies content based on URL paths
  - Detects special content (PDF links, audio)
- **Output**: `data/processed/stage03/enriched_data.jsonl`
- **Key Features**:
  - spaCy NLP processing for entity extraction
  - Keyword extraction with frequency analysis
  - Content classification using predefined tags
  - PDF and audio link detection
  - Text length limiting for performance

**Output Format**:
```json
{
  "url": "https://uconn.edu/admissions/",
  "url_hash": "a1b2c3d4e5f6...",
  "title": "Admissions | University of Connecticut",
  "text_content": "Welcome to UConn admissions...",
  "word_count": 1205,
  "entities": ["University of Connecticut", "Connecticut"],
  "keywords": ["admissions", "students", "university", "application"],
  "content_tags": ["admissions", "undergraduate"],
  "has_pdf_links": true,
  "has_audio_links": false,
  "status_code": 200,
  "content_type": "text/html; charset=utf-8",
  "enriched_at": "2025-01-15T10:40:00"
}
```

## Data Flow

```
Seed URLs → Stage 1 → Discovered URLs → Stage 2 → Valid URLs → Stage 3 → Enriched Data
   (CSV)    Discovery     (JSONL)       Validation    (JSONL)    Enrichment    (JSONL)
```

Each stage produces JSONL output that feeds into the next stage. The orchestrator can run stages independently or in sequence, with built-in batch processing and backpressure management.

## Output Files

All output files are in JSONL format (one JSON object per line):

- **`data/processed/stage01/new_urls.jsonl`** - Discovered URLs with source tracking
- **`data/processed/stage02/validated_urls.jsonl`** - URL validation results
- **`data/processed/stage03/enriched_data.jsonl`** - Content and metadata extraction

## Logging

The pipeline uses centralized logging with:

- **Console output** - Real-time progress monitoring
- **Rotating log files** - Detailed logs in `data/logs/pipeline.log`
- **Configurable levels** - Set via `--log-level` or config files
- **Library filtering** - Reduced noise from Scrapy/aiohttp logs

Log levels:
- `DEBUG` - Detailed debugging information
- `INFO` - General progress updates (default)
- `WARNING` - Important notices and recoverable errors
- `ERROR` - Critical errors and failures

## Requirements

- **Python 3.8+**
- **Dependencies**:
  - `scrapy>=2.5.0` - Web crawling framework
  - `aiohttp>=3.8.0` - Async HTTP client for validation
  - `spacy>=3.4.0` - NLP processing (requires en_core_web_sm model)
  - `w3lib>=1.22.0` - URL canonicalization utilities
  - `PyYAML>=6.0.0` - YAML configuration parsing

Install all dependencies:
```bash
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

## Development

### Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run specific test module
python -m pytest tests/utils/

# Run with coverage
python -m pytest --cov=src tests/
```

### Code Organization

The codebase follows a clean architecture:

- **`src/orchestrator/`** - High-level pipeline coordination
- **`src/stage*/`** - Stage-specific implementation
- **`src/common/`** - Shared utilities and schemas
- **Separation of concerns** - Each module has a single responsibility
- **Async-first design** - Built for concurrent processing
- **Config-driven** - Behavior controlled via YAML files