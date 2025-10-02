# UConn Scraper Architecture

## Overview

The UConn Web Scraping Pipeline is a three-stage asyncio-based system for discovering, validating, and enriching web content from the uconn.edu domain. The architecture follows a producer-consumer pattern with persistent checkpointing and backpressure handling.

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Orchestrator (async)                        │
│                    src/orchestrator/main.py                      │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────────┐
        │          Configuration Manager              │
        │         (config/development.yml)            │
        └─────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│   Stage 1     │───▶│   Stage 2     │───▶│   Stage 3     │
│  Discovery    │    │  Validation   │    │  Enrichment   │
│   (Scrapy)    │    │   (aiohttp)   │    │   (Scrapy)    │
└───────────────┘    └───────────────┘    └───────────────┘
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│  new_urls     │    │  validated_   │    │  enriched_    │
│    .jsonl     │    │   urls.jsonl  │    │  data.jsonl   │
└───────────────┘    └───────────────┘    └───────────────┘
```

## Core Components

### 1. Orchestrator (`src/orchestrator/`)

**Purpose**: Manages the pipeline lifecycle, coordinates stages, and handles inter-stage communication.

**Key Classes**:
- `PipelineOrchestrator`: Main coordinator
- `BatchQueue`: Async queue with backpressure
- `Config`: YAML configuration loader with validation

**Responsibilities**:
- Load and validate configuration
- Initialize data directories
- Run stages sequentially or individually
- Manage inter-stage queues
- Handle graceful shutdown
- Clean up temporary files

### 2. Stage 1 - Discovery (`src/stage1/`)

**Purpose**: Discovers URLs through crawling and dynamic source analysis.

**Technology**: Scrapy (Twisted async framework)

**Components**:
- `DiscoverySpider`: Main spider class
  - Breadth-first crawling with configurable depth
  - LinkExtractor for HTML links
  - Dynamic URL discovery from:
    - Data attributes (`data-url`, `data-endpoint`, etc.)
    - Inline JavaScript
    - JSON script blocks
    - Form actions
  - Pagination pattern generation

- `Stage1Pipeline`: Output pipeline
  - Writes to JSONL format
  - Deduplication via SQLite URLCache
  - Checkpoint management

**Data Flow**:
```
CSV seeds → DiscoverySpider → LinkExtractor
                          ↓
                    Dynamic Discovery
                          ↓
                    Canonicalization
                          ↓
                      URLCache (SQLite)
                          ↓
                    Stage1Pipeline → JSONL
```

### 3. Stage 2 - Validation (`src/stage2/`)

**Purpose**: Validates URL availability and collects response metadata.

**Technology**: aiohttp (asyncio HTTP client)

**Components**:
- `URLValidator`: Async validator
  - HEAD request with GET fallback
  - Concurrent batch processing
  - Connection pooling
  - SSL context configuration
  - Retry logic

- `CheckpointManager`: Resumable validation
  - Tracks progress per batch
  - Validates checkpoint freshness
  - Input file hash verification

**Data Flow**:
```
Stage1 JSONL → BatchQueue (async) → URLValidator (concurrent)
                                          ↓
                                    HEAD/GET requests
                                          ↓
                                   ValidationResult
                                          ↓
                                    Streaming write
                                          ↓
                                    Stage2 JSONL
```

**Idempotency**: Tracks processed URL hashes to skip duplicates on re-run.

### 4. Stage 3 - Enrichment (`src/stage3/`)

**Purpose**: Extracts and enriches content with NLP analysis.

**Technology**: Scrapy + spaCy/transformers

**Components**:
- `EnrichmentSpider`: Content extraction spider
  - Full page GET requests
  - HTML to text conversion
  - Link extraction (PDF, audio)

- `NLPRegistry`: NLP pipeline manager
  - spaCy for entities and keywords
  - Optional transformer models
  - Device selection (CPU/CUDA/MPS/MLX)
  - Fallback handling

- `Stage3Pipeline`: Output pipeline with pluggable storage writers
  - Content cleaning and schema versioning
  - JSONL/SQLite/Parquet/S3 outputs with configurable rotation/compression

**Data Flow**:
```
Stage2 JSONL → EnrichmentSpider → HTML parsing
                                       ↓
                                  Text extraction
                                       ↓
                                  NLP processing
                                       ↓
                                 EnrichmentItem
                                       ↓
                                Stage3Pipeline → JSONL
```

## Common Infrastructure (`src/common/`)

### Data Schemas (`schemas.py`)

All stages use versioned dataclasses:
- `DiscoveryItem` (v2.0)
- `ValidationResult` (v2.0)
- `EnrichmentItem` (v2.0)
- `SchemaRegistry`: Version compatibility management

### Logging (`logging.py`)

- Structured JSON logging support
- Rotating file handlers
- Separate error logs
- Context-aware logger adapter

### URL Utilities (`urls.py`)

- Canonicalization with path normalization
- Domain validation
- Hash generation

### Checkpointing (`checkpoints.py`)

- `BatchCheckpoint`: Per-stage progress tracking
- `CheckpointManager`: Multi-stage coordination
- Staleness detection
- File integrity validation

### NLP (`nlp.py`)

- `NLPRegistry`: Centralized model management
- `NLPSettings`: Configuration dataclass
- Device auto-detection
- Graceful degradation on missing models

## Data Flow Patterns

### 1. Batch Processing with Backpressure

```python
# Producer
async def populate_queue():
    for item in load_data():
        await queue.put(item)  # Blocks when full
    queue.mark_producer_done()

# Consumer
async def process_queue():
    while True:
        batch = await queue.get_batch_or_wait(timeout=2.0)
        if not batch:
            break
        await process_batch(batch)
```

### 2. Checkpoint-Based Resumption

```python
# Start
checkpoint.start_batch(stage="stage2", batch_id=42)

# Progress
for i, item in enumerate(batch):
    result = await process(item)
    checkpoint.update_progress(line=i, url_hash=item.hash)

# Complete
checkpoint.complete_batch(total_processed=len(batch))
```

### 3. Configuration Hierarchy

```
Environment Variables
         ↓
config/<env>.yml
         ↓
    Config class
         ↓
 Stage-specific configs
```

## Concurrency Model

### Stage 1 (Scrapy)
- Twisted reactor (event loop)
- Configurable concurrent requests
- Per-domain rate limiting
- Connection pooling

### Stage 2 (aiohttp)
- asyncio event loop
- Concurrent batch workers
- Connection pooling with limits
- Timeout management

### Stage 3 (Scrapy)
- Twisted reactor
- Sequential processing (NLP-bound)
- Configurable concurrency

## Error Handling

### Levels:
1. **Item-level**: Log and continue
2. **Batch-level**: Mark checkpoint failed
3. **Stage-level**: Graceful shutdown with cleanup
4. **Pipeline-level**: Signal handling, cleanup

### Strategies:
- Retry with exponential backoff (HTTP)
- Fallback models (NLP)
- Checkpoint recovery
- Structured error logging

## Storage Architecture

### File System Layout:
```
data/
├── raw/              # Input seeds
├── processed/        # Stage outputs (JSONL)
│   ├── stage01/
│   ├── stage02/
│   └── stage03/
├── cache/            # URLCache SQLite, Scrapy cache
├── checkpoints/      # JSON checkpoint files
├── logs/             # Rotating logs
└── temp/             # Temporary files (auto-cleaned)
```

### Formats:
- **Input**: CSV (seeds)
- **Inter-stage**: JSONL (newline-delimited JSON)
- **Cache**: SQLite (URLCache)
- **Checkpoints**: JSON
- **Logs**: Plain text or JSON

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| HTTP Client | aiohttp, Scrapy | Async HTTP |
| HTML Parsing | BeautifulSoup4, lxml | Content extraction |
| NLP | spaCy, transformers | Entity/keyword extraction |
| Async | asyncio, Twisted | Concurrency |
| Config | PyYAML | Configuration management |
| Data | pandas, numpy | Data processing |
| Testing | pytest | Unit/integration tests |
| CLI | Click | Command-line interface |

## Performance Characteristics

- **Stage 1**: ~1000-5000 URLs/minute (network-bound)
- **Stage 2**: ~500-1000 validations/minute (network-bound)
- **Stage 3**: ~100-500 enrichments/minute (NLP-bound)

### Bottlenecks:
1. Network latency (Stages 1 & 2)
2. NLP processing (Stage 3)
3. Disk I/O (all stages)

### Optimizations:
- Connection pooling and keepalive
- Concurrent request batching
- Streaming I/O
- In-memory deduplication
- SQLite caching

## Extension Points

1. **Custom Spiders**: Extend `DiscoverySpider` or `EnrichmentSpider`
2. **Pipelines**: Add Scrapy item pipelines
3. **NLP Backends**: Register custom NLP processors
4. **Validators**: Add custom validation logic
5. **Output Formats**: Add custom exporters

## Security Considerations

- Permissive SSL for internal domains (configurable)
- No credential storage
- User-agent rotation
- Rate limiting
- Input validation
- Checkpoint integrity checks
