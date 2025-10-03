# Project Internals

This document explains key technical concepts, implementation details, and design decisions within the UConn Web Scraping Pipeline. Use this as a reference for understanding how the pipeline works under the hood.

---

## Table of Contents
- [Frozen Requirements Strategy](#frozen-requirements-strategy)
- [Stage 3 Enrichment Workflow](#stage-3-enrichment-workflow)
- [Output Schema and Data Structure](#output-schema-and-data-structure)
- [Checkpoint System](#checkpoint-system)
- [Configuration Management](#configuration-management)
- [Storage Backends](#storage-backends)
- [Concurrency and Async Architecture](#concurrency-and-async-architecture)
- [Platform Compatibility](#platform-compatibility)

---

## Frozen Requirements Strategy

### Why Frozen Dependencies?

The `requirements.txt` file uses **unpinned versions** for most dependencies (e.g., `scrapy`, `aiohttp`) to allow flexibility during development. However, this creates risks:

1. **Reproducibility**: Different environments may install different versions
2. **Compatibility**: New dependency versions can introduce breaking changes
3. **Security**: Uncontrolled updates may pull in vulnerable packages

### Current Approach

**Development**: `requirements.txt` uses unpinned versions for flexibility
```bash
scrapy
aiohttp
spacy
```

**Future Production Strategy** (not yet implemented):
- Generate a `requirements-lock.txt` using `pip freeze` after testing
- Use `pip-tools` or `poetry` for deterministic dependency resolution
- Pin critical dependencies (e.g., `spacy==3.7.2`) while allowing minor updates for utilities

### Recommendations

For production deployments, consider:
1. **Lock file generation**: `pip freeze > requirements-lock.txt` after successful testing
2. **Dependency scanning**: Use tools like `safety` or `dependabot` to monitor vulnerabilities
3. **Version ranges**: Pin major versions but allow patch updates (e.g., `scrapy>=2.11,<3.0`)

---

## Stage 3 Enrichment Workflow

Stage 3 is the most complex stage in the pipeline. It transforms validated URLs into structured, enriched content records.

### High-Level Flow

```
Validated URLs (Stage 2)
    → Filter (2xx/3xx status only)
    → Fetch full HTML content
    → Extract text and metadata
    → NLP processing (entities, keywords)
    → Schema validation
    → Storage backend (JSONL/SQLite/Parquet/S3)
```

### Detailed Workflow

#### 1. Input Filtering
- **Source**: `data/processed/stage02/validation_output.jsonl`
- **Filter criteria**: Only URLs with `status_code` in 200-399 range
- **Why**: Avoid processing error pages, redirects to external domains, or unreachable content

#### 2. Content Fetching
- Uses `aiohttp` for async HTTP requests
- Implements retry logic with exponential backoff (3 attempts)
- Respects `User-Agent` configuration from `config/*.yml`
- Handles non-text content gracefully (skips binary files)

#### 3. Text Extraction
**Primary tool**: BeautifulSoup4 with lxml parser

**Extraction steps**:
1. Parse HTML into DOM tree
2. Extract `<title>` tag for page title
3. Remove non-content elements: `<script>`, `<style>`, `<nav>`, `<footer>`
4. Extract visible text from `<body>`
5. Clean whitespace and normalize Unicode

**Special handling**:
- PDF links detected via `href` ending in `.pdf`
- Audio/video links detected via `<audio>`, `<video>`, or common media extensions
- Metadata flags: `has_pdf_links`, `has_audio_links`

#### 4. NLP Processing
**Engine**: spaCy (`en_core_web_sm` model)

**Processing pipeline**:
1. **Tokenization**: Split text into sentences and tokens
2. **Entity Recognition**: Extract named entities (PERSON, ORG, GPE, DATE, etc.)
3. **Keyword Extraction**:
   - Use TF-IDF-like scoring
   - Filter stopwords and common words
   - Select top 15 keywords by frequency and relevance
4. **Content Tagging**: (Placeholder for future taxonomy-based classification)

**Performance optimization**:
- Text truncation: Process first 10,000 characters to avoid memory issues
- Batch processing: Process multiple documents in parallel (configurable workers)
- Model caching: spaCy model loaded once and reused

#### 5. Schema Validation
All enriched items conform to `EnrichmentItem` schema ([src/common/schemas.py](../src/common/schemas.py)):

```python
{
    "url": str,              # Original URL
    "url_hash": str,         # SHA-256 hash for deduplication
    "title": str,            # Page title
    "text_content": str,     # Extracted body text
    "word_count": int,       # Number of words in text_content
    "entities": List[str],   # Named entities from NLP
    "keywords": List[str],   # Top keywords (max 15)
    "content_tags": List[str],  # Future: taxonomy categories
    "has_pdf_links": bool,   # Contains PDF links
    "has_audio_links": bool, # Contains audio/video
    "status_code": int,      # HTTP status from Stage 2
    "content_type": str,     # MIME type
    "enriched_at": str,      # ISO timestamp
    "processed_at": str      # ISO timestamp
}
```

#### 6. Storage
See [Storage Backends](#storage-backends) section below.

### Why Stage 3 Doesn't Work Through Orchestrator (Known Bug)

**Current Issue**: Running `python main.py --stage 3` fails, but running Scrapy directly works:
```bash
# Fails
python main.py --env development --stage 3

# Works
cd Scraping_project/src/stage3
scrapy crawl enrichment
```

**Root cause**: Configuration mismatch between orchestrator and Scrapy settings
- Orchestrator passes config via environment variables
- Scrapy expects settings in `src/settings.py`
- Path resolution differs between orchestrator and direct Scrapy invocation

**Workaround**: Use direct Scrapy commands for Stage 3 until orchestrator integration is fixed

---

## Output Schema and Data Structure

### File Locations

| Stage | Output File | Schema |
|-------|-------------|--------|
| Stage 1 | `data/processed/stage01/new_urls.jsonl` | `DiscoveredURL` |
| Stage 2 | `data/processed/stage02/validation_output.jsonl` | `ValidationResult` |
| Stage 3 | `data/processed/stage03/enriched_content.jsonl` | `EnrichmentItem` |

### Schema Evolution

**Stage 1 → Stage 2**:
```
DiscoveredURL {url, source, depth}
    → ValidationResult {url, status_code, content_length, latency, ...}
```

**Stage 2 → Stage 3**:
```
ValidationResult {url, status_code, ...}
    → EnrichmentItem {url, title, text_content, entities, keywords, ...}
```

### Example: Final Enriched Output

```json
{
  "url": "https://health.uconn.edu/",
  "url_hash": "8cfc602a091fe1ec61f9e56d9fb2a49be7c60cf4e9c27aadf8b717b4a4e59575",
  "title": "Home | UConn Health",
  "text_content": "Skip Navigation Give Search UConn Health...",
  "word_count": 259,
  "entities": [
    "UConn Health",
    "Connecticut",
    "John Dempsey Hospital"
  ],
  "keywords": [
    "uconn",
    "health",
    "patient",
    "care",
    "research",
    "connecticut",
    "academics"
  ],
  "content_tags": [],
  "has_pdf_links": false,
  "has_audio_links": false,
  "status_code": 200,
  "content_type": "text/html",
  "enriched_at": "2025-09-30T14:05:39.082064",
  "processed_at": "2025-09-30T14:05:39.082270"
}
```

### Field Descriptions

| Field | Type | Description | Source |
|-------|------|-------------|--------|
| `url` | string | Original page URL | Stage 2 input |
| `url_hash` | string | SHA-256 hash of URL (for deduplication) | Generated in Stage 3 |
| `title` | string | HTML `<title>` content | Extracted from HTML |
| `text_content` | string | Clean body text without HTML tags | BeautifulSoup extraction |
| `word_count` | integer | Number of words in `text_content` | Calculated |
| `entities` | array[string] | Named entities (people, orgs, places) | spaCy NER |
| `keywords` | array[string] | Top 15 relevant keywords | TF-IDF + frequency |
| `content_tags` | array[string] | Taxonomy categories (future feature) | Planned: zero-shot classification |
| `has_pdf_links` | boolean | Contains links to PDF files | Link analysis |
| `has_audio_links` | boolean | Contains audio/video elements | Media detection |
| `status_code` | integer | HTTP response code | From Stage 2 |
| `content_type` | string | MIME type (e.g., "text/html") | HTTP headers |
| `enriched_at` | string | ISO timestamp when NLP ran | Stage 3 processing time |
| `processed_at` | string | ISO timestamp when saved | Storage write time |

---

## Checkpoint System

The pipeline uses a checkpoint system to enable resumability after failures or interruptions.

### How It Works

**Checkpoint files**: `data/checkpoints/stage{N}_checkpoint.json`

**Structure**:
```json
{
  "stage": 1,
  "last_processed_url": "https://uconn.edu/academics/",
  "processed_count": 1523,
  "timestamp": "2025-10-03T10:45:32",
  "metadata": {
    "start_time": "2025-10-03T09:00:00",
    "errors": 12
  }
}
```

### Resume Behavior

When a stage starts:
1. Check if checkpoint file exists
2. Load `last_processed_url` and `processed_count`
3. Skip already-processed URLs
4. Continue from next unprocessed item

**Implementation**: [src/common/checkpoints.py](../src/common/checkpoints.py)

### Checkpoint Strategies by Stage

| Stage | Checkpoint Trigger | Resume Strategy |
|-------|-------------------|-----------------|
| Stage 1 | Every 100 URLs discovered | Skip URLs already in `new_urls.jsonl` |
| Stage 2 | Every 50 validations | Read validation output, skip validated URLs |
| Stage 3 | Every 25 enrichments | Read enriched output, skip processed URLs |

### Manual Checkpoint Management

```bash
# View checkpoint status
python tools/checkpoint_manager_cli.py status

# Reset specific stage
python tools/checkpoint_manager_cli.py reset --stage 2

# Backup checkpoints
cp -r data/checkpoints/ data/checkpoints.backup/
```

---

## Configuration Management

### Configuration Hierarchy

Pipeline configuration merges settings in this order (later overrides earlier):

1. **Defaults** in code (`src/orchestrator/config.py`)
2. **Environment YAML** (`config/development.yml` or `config/production.yml`)
3. **Environment variables** (`STAGE1_MAX_DEPTH`, `SCRAPY_CONCURRENT_REQUESTS`)
4. **CLI arguments** (`--stage`, `--env`)

### Environment Files

**Development** (`config/development.yml`):
- Lower concurrency for local testing
- Verbose logging
- Short timeouts
- Small batch sizes

**Production** (`config/production.yml`):
- Higher concurrency for throughput
- Structured logging (JSON format)
- Longer timeouts
- Large batch sizes with rotation

### Key Configuration Sections

#### Stage 1 (Discovery)
```yaml
stages:
  discovery:
    max_depth: 3
    concurrent_requests: 16
    timeout: 30
    allowed_domains:
      - uconn.edu
      - health.uconn.edu
```

#### Stage 2 (Validation)
```yaml
stages:
  validation:
    max_workers: 50
    timeout: 15
    retry_attempts: 3
    connector_limit: 100
```

#### Stage 3 (Enrichment)
```yaml
stages:
  enrichment:
    output_file: data/processed/stage03/enrichment_output.jsonl
    storage:
      backend: jsonl
      options:
        path: data/processed/stage03/enrichment_output.jsonl
      rotation:
        max_items: 5000
      compression:
        codec: none  # or 'gzip'
```

### Environment Variable Overrides

```bash
# Override max depth
export STAGE1_MAX_DEPTH=5

# Override concurrency
export SCRAPY_CONCURRENT_REQUESTS=32

# Override storage backend
export STAGE3_STORAGE_BACKEND=sqlite

# Run with overrides
python main.py --env development --stage all
```

---

## Storage Backends

Stage 3 supports pluggable storage backends via the `enrichment.storage` configuration.

### Available Backends

#### 1. JSONL (Default)
**Format**: Newline-delimited JSON
**Best for**: Simple deployments, easy inspection, append-only writes

```yaml
storage:
  backend: jsonl
  options:
    path: data/processed/stage03/enriched_content.jsonl
  rotation:
    max_items: 5000  # Create new file after N items
  compression:
    codec: gzip  # Optional: compress output
```

**Output**: `enriched_content.jsonl`, `enriched_content.0001.jsonl.gz`, etc.

#### 2. SQLite
**Format**: Relational database
**Best for**: Local analysis, SQL queries, data exploration

```yaml
storage:
  backend: sqlite
  options:
    path: data/processed/stage03/enrichment.db
    table_name: enrichment_items
```

**Schema**: Auto-created table with columns matching `EnrichmentItem` fields

#### 3. Parquet
**Format**: Columnar storage (Apache Parquet)
**Best for**: Analytics pipelines, data science workflows, compression

```yaml
storage:
  backend: parquet
  options:
    path: data/processed/stage03/enriched_content.parquet
  rotation:
    max_items: 10000
```

**Requirements**: `pip install pyarrow`

#### 4. S3
**Format**: JSONL uploaded to AWS S3
**Best for**: Cloud deployments, distributed processing, archival

```yaml
storage:
  backend: s3
  options:
    bucket: my-scraping-bucket
    prefix: uconn/enriched/
    region: us-east-1
  rotation:
    max_items: 5000
  compression:
    codec: gzip
```

**Requirements**:
- `pip install boto3`
- AWS credentials configured (`~/.aws/credentials` or environment variables)

**Output**: `s3://my-scraping-bucket/uconn/enriched/enriched_content.0001.jsonl.gz`

### Storage Rotation

For large crawls, storage backends support automatic file rotation:

```yaml
rotation:
  max_items: 5000      # Rotate after N items
  max_size_mb: 100     # Or rotate after N megabytes
  timestamp_suffix: true  # Add timestamp to rotated files
```

**Example output**:
```
enriched_content.jsonl
enriched_content.20251003_104532.jsonl
enriched_content.20251003_115612.jsonl
```

---

## Concurrency and Async Architecture

The pipeline uses different concurrency models for each stage:

### Stage 1: Scrapy's Reactor Pattern
- **Framework**: Twisted reactor (event-driven)
- **Concurrency**: Configurable via `SCRAPY_CONCURRENT_REQUESTS`
- **Default**: 16 concurrent requests
- **Throttling**: Scrapy's AutoThrottle extension adjusts based on server response times

### Stage 2: AsyncIO with aiohttp
- **Framework**: Python asyncio
- **Concurrency**: Configurable via `max_workers` (default: 50)
- **Connection pooling**: `TCPConnector` with limit = 2 × max_workers
- **Semaphore**: Prevents overwhelming target servers
- **Event loop**: Proactor on Windows, selector on Unix

**Key optimization**:
```python
connector = aiohttp.TCPConnector(
    limit=max_workers * 2,
    limit_per_host=10,
    ttl_dns_cache=300
)
```

### Stage 3: Hybrid Scrapy + Async
- **Fetching**: Scrapy reactor for HTTP requests
- **Processing**: Async workers for NLP pipeline
- **Parallelism**: Configurable via `CONCURRENT_ITEMS` in settings
- **Backpressure**: Item pipeline signals when storage is full

### Concurrency Tuning Guidelines

| Resource Constraint | Recommended Action |
|---------------------|-------------------|
| High CPU usage | Reduce NLP workers (Stage 3) |
| High memory usage | Reduce batch sizes, enable streaming |
| Network saturation | Reduce concurrent requests (Stage 1/2) |
| Target server errors (429, 503) | Lower concurrency, add delays |
| Slow NLP processing | Increase workers, use smaller spaCy model |

---

## Platform Compatibility

The pipeline is designed to run on **Windows**, **Linux**, and **macOS**.

### Windows-Specific Considerations

#### Event Loop
- **Issue**: Windows uses `ProactorEventLoop` by default (Python 3.8+)
- **Impact**: Some asyncio features behave differently
- **Solution**: Explicit event loop policy in `src/stage2/validator.py`

```python
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
```

#### Path Handling
- **Separator**: Use `pathlib.Path` or forward slashes (not backslashes)
- **Long paths**: Enable via registry if paths exceed 260 characters

#### File Locks
- **JSONL writing**: Use `mode='a'` with explicit flush to avoid locks
- **Checkpoint files**: Use atomic writes (write to temp, then rename)

### Unix-Specific Optimizations

#### File Descriptors
- **Increase limits**: `ulimit -n 4096` for high concurrency
- **Check current**: `ulimit -n`

#### Selector Loop
- **Default**: `SelectorEventLoop` (epoll on Linux, kqueue on macOS)
- **Performance**: Better than Proactor for I/O-heavy workloads

### Cross-Platform Testing

Run platform-specific tests:
```bash
# Windows
python -m pytest tests/ -k windows

# Unix
python -m pytest tests/ -k unix

# All platforms
python -m pytest tests/
```

---

## Troubleshooting

### Common Issues

#### 1. Stage 3 CLI Bug
**Symptom**: `python main.py --stage 3` fails with import errors
**Solution**: Run Stage 3 directly via Scrapy:
```bash
cd Scraping_project/src/stage3
scrapy crawl enrichment
```

#### 2. spaCy Model Not Found
**Symptom**: `OSError: Can't find model 'en_core_web_sm'`
**Solution**: Download the model:
```bash
python -m spacy download en_core_web_sm
```

#### 3. Memory Issues on Large Crawls
**Symptom**: Process killed or OOM errors
**Solutions**:
- Enable storage rotation (`max_items: 1000`)
- Reduce batch size in Stage 3
- Use Parquet backend for better compression
- Process in smaller chunks (reduce `max_depth` in Stage 1)

#### 4. Checkpoint Corruption
**Symptom**: JSON decode errors when loading checkpoint
**Solution**: Delete checkpoint and restart:
```bash
rm data/checkpoints/stage3_checkpoint.json
python main.py --env development --stage 3
```

---

## Next Steps

- Read [../README.md](../README.md) for high-level overview
- Check [../config/development.yml](../config/development.yml) for configuration examples
- Review [../tests/](../tests/) for usage examples
- See [SPRINT_BACKLOG.md](../SPRINT_BACKLOG.md) for planned enhancements
