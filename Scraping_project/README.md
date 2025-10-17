# Web Scraping Project - Multi-Stage Pipeline

A production-grade web scraping pipeline designed for large-scale institutional data collection and analysis.

## Features

### Multi-Stage Architecture
- **Stage 1**: Discovery and URL extraction using Scrapy spiders
- **Stage 2**: Content analysis and validation
- **Stage 3**: Entity summarization and aggregation
- **Stage 4**: Advanced processing and enrichment

### Metadata Extraction Pipeline (NEW)
- Extract keywords from content using YAKE or spaCy
- Entity extraction (persons, organizations, locations)
- Batch processing for efficiency
- Saves enriched data to `metadata_queue` Delta table
- Operates between Stage 2 and Stage 3 for improved downstream analysis

### Centralized URL Filtering
- Single source of truth for URL filtering logic in `url_processor.should_follow_url()`
- Consistent filtering across all spiders
- Liberal Stage 1 policy: captures everything except binary/media assets
- Comprehensive test coverage (75+ tests)

### Data Storage
- **Delta Lake**: Primary data lake for scalable storage
- **PostgreSQL**: Structured data and metadata
- **Redis**: Distributed deduplication and caching
- **Kafka**: Real-time event streaming

### Monitoring & Observability
- Prometheus metrics for pipeline health
- Grafana dashboards for visualization
- Comprehensive logging and alerting

## Quick Start

### Prerequisites
- Docker and Docker Compose
- Python 3.10+
- Access to Kafka, PostgreSQL, Redis (or use provided docker-compose)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd Scraping_project
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Start infrastructure (optional - for local development):
```bash
docker-compose up -d
```

### Running the Pipeline

#### Option 1: Full Pipeline (All Stages)
```bash
python start.py --env local
```

#### Option 2: Kubernetes Deployment
```bash
python start.py --env k8s --stage pipeline
```

#### Option 3: Individual Stages
```bash
# Run only Stage 1 (Discovery)
python cli.py scrapy --spiders scout

# Run full multi-stage pipeline
python cli.py pipeline

# Run with stage skipping
python cli.py pipeline --skip-stage1 --stage2-workers 200
```

### Resetting the Environment

For deterministic, repeatable runs:

```bash
# Reset Delta Lake and reload seed URLs
python start.py --env local --reset-delta

# Or use the CLI directly
python cli.py reset --force
```

This will:
1. Delete all Delta Lake tables
2. Recreate directory structure
3. Reload seed URLs from `data/raw/uconn_urls.csv`

## Architecture

### Stage 1: Discovery
- **ScoutSpider**: Rapid breadth-first discovery
- **DepthSpider**: Depth-first focused crawling
- **JSSpider**: JavaScript-rendered content extraction

**Output**: `seed_urls`, `js_spider_queue`, `stage2_queue`

### Stage 2: Content Analysis
- Validates and analyzes discovered content
- Filters and classifies pages
- Extracts structured data

**Output**: Raw content for Stage 3

### Metadata Extraction (Between Stage 2 & 3)
- **MetadataExtractionPipeline**: Enriches content with keywords and entities
- Configurable extractors (YAKE, spaCy, or simple fallback)
- Batch processing (default: 100 items)

**Output**: `metadata_queue` Delta table

Configuration:
```python
# In Scrapy settings.py
METADATA_EXTRACTION_ENABLED = True
METADATA_EXTRACTOR_TYPE = "yake"  # or "spacy" or "simple"
METADATA_BATCH_SIZE = 100
METADATA_MAX_KEYWORDS = 10
```

### Stage 3: Summarization
- Entity grouping and recency-weighted aggregation
- LLM-based summarization
- Temporal relevance scoring

**Output**: Entity summaries

### Stage 4: Advanced Processing
- ML-based classification
- Relationship extraction
- Final enrichment

## URL Filtering

All URL filtering is centralized in `src/common/url_processor.py`:

```python
from src.common.url_processor import should_follow_url

# Check if a URL should be crawled
if should_follow_url(url):
    # Process URL
    pass
```

**Filtering Policy**:
- **Skip**: Pure binary/media assets (`.jpg`, `.mp4`, `.woff`, etc.)
- **Allow**: Documents (`.pdf`, `.docx`), JavaScript (`.js`), APIs, most HTML
- **Block**: Problematic endpoints (`/wp-login.php`, `/checkout`)

See `src/common/url_processor.py` for the complete filter list.

## Data Pipeline

```
Seed URLs → Stage 1 (Discovery) → Stage 2 (Analysis) → Metadata Extraction →
Stage 3 (Summarization) → Stage 4 (Advanced) → Final Output
```

### Delta Lake Tables

| Table | Description | Populated By |
|-------|-------------|--------------|
| `seed_urls` | Initial seed URLs | Reset script |
| `discovered_urls` | All discovered URLs | Stage 1 spiders |
| `js_spider_queue` | JS-heavy pages | ScoutSpider |
| `stage2_queue` | Pages for analysis | ScoutSpider |
| `metadata_queue` | Extracted metadata | MetadataExtractionPipeline |
| `stage1_offsite_candidates` | External links | Stage 1 spiders |

## Configuration

### Environment Variables

```bash
# Kafka
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_TOPIC=scraped_items

# PostgreSQL
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=scraping_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=secret

# Redis
REDIS_HOST=localhost
REDIS_PORT=6379

# Delta Lake
DELTA_LAKE_PATH=/data/delta_lake
```

### Spider Configuration

Spiders are configured via `src/settings.py` or custom settings:

```python
custom_settings = {
    'CONCURRENT_REQUESTS': 16,
    'DOWNLOAD_DELAY': 1.0,
    'METADATA_EXTRACTION_ENABLED': True,
    'METADATA_EXTRACTOR_TYPE': 'yake',
}
```

## Testing

### Run All Tests
```bash
pytest
```

### Run Specific Test Suites
```bash
# URL filtering tests
pytest tests/common/test_url_processor.py -v

# Metadata extraction tests
pytest tests/integration/test_metadata_queue.py -v

# Spider tests
pytest tests/unit/stage1/ -v

# Integration tests
pytest tests/integration/ -v
```

### Test Coverage
```bash
pytest --cov=src --cov-report=html
```

## Monitoring

### Prometheus Metrics

Available at `http://localhost:9091/metrics`:

- `scrapy_items_scraped_total`: Total items scraped
- `new_urls_found_per_minute`: URL discovery rate
- `average_file_size_bytes`: Average response size
- `offsite_links_found`: External links discovered
- `stage2_items_processed`: Stage 2 throughput
- `stage3_summaries_generated`: Stage 3 summaries

### Grafana Dashboards

Access dashboards at `http://localhost:3000`:

- Pipeline Overview
- Spider Performance
- Queue Health
- Error Rates

Default credentials: `admin / <GRAFANA_ADMIN_PASSWORD from .env>`

## Utility Commands

### Export Data
```bash
# Export specific table
python cli.py export --table seed_urls --output exports/ --format csv

# Export all tables
python cli.py export --output exports/
```

### Health Check
```bash
python cli.py health
```

### Validate Tables
```bash
python cli.py validate
```

### Clean Temporary Files
```bash
python cli.py clean --verbose
```

## Development

### Project Structure

```
Scraping_project/
├── src/
│   ├── common/          # Shared utilities
│   │   ├── delta_lake.py
│   │   ├── url_processor.py  # Centralized URL filtering
│   │   └── ...
│   ├── stage1/          # Discovery spiders
│   │   ├── scout_spider.py
│   │   ├── depth_spider.py
│   │   └── js_spider.py
│   ├── stage2/          # Content analysis
│   ├── stage3/          # Summarization
│   ├── pipelines.py     # Scrapy pipelines (includes MetadataExtractionPipeline)
│   └── settings.py      # Configuration
├── tests/
│   ├── unit/           # Unit tests
│   ├── integration/    # Integration tests
│   └── performance/    # Performance tests
├── data/
│   └── raw/
│       └── uconn_urls.csv  # Seed URL file
├── scripts/
│   └── reset_lake.py   # Delta Lake reset utility
├── cli.py              # CLI entry point
├── start.py            # Startup script
└── docker-compose.yml  # Infrastructure setup
```

### Adding a New Spider

1. Create spider in `src/stage1/`:
```python
from src.stage1.base_spider import BaseSpider

class MySpider(BaseSpider):
    name = "my_spider"

    def parse(self, response):
        # Your parsing logic
        pass
```

2. Register in settings
3. Add tests in `tests/unit/stage1/`

### Adding a New Pipeline

1. Create pipeline class in `src/pipelines.py`:
```python
class MyPipeline:
    def process_item(self, item, spider):
        # Your processing logic
        return item
```

2. Configure in settings:
```python
ITEM_PIPELINES = {
    'src.pipelines.MyPipeline': 400,
}
```

## Troubleshooting

### Common Issues

**Issue**: Delta Lake tables not found
```bash
# Solution: Reset Delta Lake
python cli.py reset --force
```

**Issue**: Redis connection errors
```bash
# Solution: Check Redis is running
docker-compose ps redis
docker-compose logs redis
```

**Issue**: Kafka not available
```bash
# Solution: Check Kafka broker
docker-compose ps kafka
docker-compose logs kafka
```

**Issue**: Tests failing after URL filter changes
```bash
# Solution: Run URL processor tests to identify issues
pytest tests/common/test_url_processor.py -v
```

## Recent Changes

### Metadata Extraction Pipeline (Latest)
- Added `MetadataExtractionPipeline` for keyword/entity extraction
- Supports YAKE, spaCy, and simple fallback extractors
- Saves to new `metadata_queue` Delta table
- Comprehensive integration tests

### URL Filtering Consolidation
- Centralized all filtering logic in `url_processor.should_follow_url()`
- Removed duplicate filter definitions from spiders
- Updated `BaseSpider`, `ScoutSpider`, `DepthSpider` to use centralized logic
- 75+ test cases for filtering edge cases

### Test Organization
- Moved all root-level test files to `tests/integration/`
- Consistent test directory structure
- Better test discovery and organization

### Deterministic Runs
- Added `--reset-delta` flag to `start.py`
- CLI reset command for reproducible environments
- Automatic seed URL loading

## Contributing

1. Create a feature branch
2. Write tests for new functionality
3. Ensure all tests pass: `pytest`
4. Submit a pull request

## License

[Your License Here]

## Contact

[Your Contact Information]
