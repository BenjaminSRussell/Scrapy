# UConn Web Scraping Pipeline

A four-stage web scraping pipeline with intelligent quality control, Delta Lake storage, and dual-model summarization.

## Features

✅ **Stage 1: Ultra Discovery** - Finds ALL URLs (standard, hidden, obfuscated, JS-rendered)
✅ **Stage 2: Intelligent Analysis** - Quality scoring, content extraction, document triage
✅ **Stage 3: Fast Summarization** - Async workers with lightweight DistilBART model
✅ **Stage 4: Heavy Processing** - Map-reduce chunking for massive documents
✅ **Delta Lake Storage** - ACID transactions with automatic checkpointing
✅ **OCR & Media** - EasyOCR for images, PyPDF2 for PDFs
✅ **CI/CD Pipeline** - Automated testing, linting, and security scanning

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/benjaminrussell/uconn-scraper.git
cd uconn-scraper

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers (for JS-heavy pages)
playwright install chromium

# Download transformer models (required for Stage 3 & 4)
# This downloads ~2 GB of models and may take several minutes
python setup_models.py
```

**⚠️ Important**: The `setup_models.py` step is required before running the pipeline. It downloads:
- **DistilBART** (~350 MB) for Stage 3 fast summarization
- **BART-large** (~1.6 GB) for Stage 4 large document processing

Models are cached in `~/.cache/huggingface/hub/` and only need to be downloaded once.

### Running the Pipeline

#### Option 1: Quick Test (Recommended for First Run)

```bash
# Run limited test pipeline (5 pages)
python run_full_pipeline_test.py
```

This will:
1. Crawl 5 test pages from `test_seeds.txt`
2. Analyze content quality
3. Generate summaries
4. Display results summary

#### Option 2: Full Pipeline

```bash
# Run complete pipeline with default seeds
python run_pipeline.py
```

#### Option 3: Individual Stages

```bash
# Stage 1: Discovery only
scrapy crawl scout -a seed_file=test_seeds.txt

# Stage 2: Analyze discovered pages
python -c "
import asyncio
from src.stage2.stage2_worker import Stage2Worker
asyncio.run(Stage2Worker().run())
"

# Stage 3: Summarize quality documents
python -c "
import asyncio
from src.stage3.stage3_worker import Stage3Worker
asyncio.run(Stage3Worker().run())
"

# Stage 4: Process large documents
python -m src.stage4.large_doc_processor
```

### Health Check

```bash
# Verify system health and view statistics
python health_check.py
```

## Architecture

### Four-Stage Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│ Stage 1: DISCOVERY (ScoutSpider)                           │
├─────────────────────────────────────────────────────────────┤
│ • Scrapy-based ultra-fast crawler                           │
│ • Detects JS-heavy pages → routes to Playwright            │
│ • Discovers ALL URLs (standard, hidden, obfuscated)         │
│ • Deduplication with URL hashing                            │
│ • Output: stage1_discovery table                            │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Stage 2: INTELLIGENT ANALYSIS                               │
├─────────────────────────────────────────────────────────────┤
│ • Quality scoring (word count + text-to-HTML ratio)         │
│ • Removes noise (scripts, nav, headers, footers)            │
│ • Content extraction (HTML, PDF, images via OCR)            │
│ • Document triage (massive docs → Stage 4 queue)            │
│ • Keyword extraction (YAKE)                                 │
│ • Output: stage2_page_analysis + stage4_large_docs          │
└─────────────────────────────────────────────────────────────┘
                            ↓
                    ┌───────┴───────┐
                    ↓               ↓
┌──────────────────────────┐  ┌─────────────────────────────┐
│ Stage 3: FAST SUMMARIZE  │  │ Stage 4: HEAVY PROCESSING   │
├──────────────────────────┤  ├─────────────────────────────┤
│ • Async workers (20x)    │  │ • Large document queue      │
│ • MinHash deduplication  │  │ • Map-reduce chunking       │
│ • DistilBART model       │  │ • Powerful LLM (BART-large) │
│ • For normal docs        │  │ • For 50k+ char docs        │
│ • Output: stage3_summaries│  │ • Output: stage4_summaries │
└──────────────────────────┘  └─────────────────────────────┘
```

### Quality Control Flow

```
HTML Page → Remove <script>, <style>, <nav>, <header>, <footer>
         → Extract clean text
         → Calculate metrics:
            • Word count
            • Text-to-HTML ratio
            • Quality score (0-1)
         → Triage:
            • Low quality → Skip
            • Massive (50k+ chars) → Stage 4 queue
            • Normal quality → Stage 3 processing
```

## Project Structure

```
.
├── run_pipeline.py              # Main pipeline orchestrator
├── run_full_pipeline_test.py    # Limited test run (recommended start)
├── health_check.py              # System health verification
├── requirements.txt             # All dependencies (single source of truth)
├── test_seeds.txt              # Test URLs for quick runs
│
├── src/
│   ├── common/                 # Shared utilities
│   │   ├── delta_lake.py       # Delta Lake manager (ACID storage)
│   │   └── __init__.py
│   │
│   ├── stage1/                 # URL Discovery
│   │   ├── scout_spider.py     # Main Scrapy spider
│   │   ├── js_bot.py          # Playwright renderer for JS pages
│   │   └── ultra_discovery.py  # Advanced URL extraction
│   │
│   ├── stage2/                 # Intelligent Analysis
│   │   ├── stage2_worker.py    # Async worker coordinator
│   │   └── intelligent_analyzer.py  # Quality scoring & content extraction
│   │
│   ├── stage3/                 # Fast Summarization
│   │   ├── stage3_worker.py    # Async worker with DistilBART
│   │   └── analytics.py        # Performance metrics
│   │
│   └── stage4/                 # Heavy Processing
│       ├── large_doc_processor.py  # Map-reduce for large docs
│       └── summarization.py    # LLM utilities
│
├── data/
│   ├── delta_lake/            # Delta Lake tables (auto-created)
│   │   ├── stage1_discovery/
│   │   ├── stage2_page_analysis/
│   │   ├── stage3_summaries/
│   │   └── stage4_large_docs/
│   ├── logs/                  # Pipeline logs
│   └── cache/                 # Temporary cache
│
├── tests/
│   ├── unit/                  # Unit tests
│   │   └── test_intelligent_analyzer.py
│   └── integration/           # Integration tests
│       └── test_scout_spider.py
│
└── .github/workflows/
    └── main.yml              # CI/CD pipeline
```

## Configuration

### Stage 1: Discovery Settings

Edit crawler settings in `src/stage1/scout_spider.py`:

```python
custom_settings = {
    'CONCURRENT_REQUESTS': 256,        # Parallel requests
    'DEPTH_LIMIT': 3,                 # Max crawl depth (0 = seeds only)
    'CLOSESPIDER_PAGECOUNT': 1000,    # Stop after N pages
}
```

### Stage 2: Quality Thresholds

Edit thresholds in `src/stage2/intelligent_analyzer.py`:

```python
MIN_WORD_COUNT = 50                    # Minimum words for quality
MIN_TEXT_TO_HTML_RATIO = 0.1           # Minimum content ratio
MASSIVE_DOC_THRESHOLD = 50000          # Characters to trigger Stage 4
```

### Stage 3: Summarization Settings

Edit worker settings in `src/stage3/stage3_worker.py`:

```python
max_concurrent = 20    # Concurrent summarization tasks
batch_size = 50       # Documents per batch
SIMILARITY_THRESHOLD = 0.3  # MinHash similarity (lower = more similar)
```

## Data Access

### Reading Delta Lake Tables

```python
from src.common.delta_lake import get_delta_manager

delta = get_delta_manager()

# Read discovered URLs
urls = delta.read('stage1_discovery')
print(f"Found {len(urls)} URLs")

# Read analyzed pages
pages = delta.read('stage2_page_analysis')
quality_pages = [p for p in pages if not p.get('is_low_quality')]
print(f"Quality pages: {len(quality_pages)}")

# Read summaries
summaries = delta.read('stage3_summaries')
for summary in summaries[:5]:
    print(f"{summary['url']}: {summary['summary'][:100]}...")
```

### Delta Lake Features

- ✅ ACID transactions
- ✅ Time travel (version history)
- ✅ Automatic checkpointing
- ✅ Schema enforcement
- ✅ Efficient columnar storage

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run specific test suite
pytest tests/unit/ -v                          # Unit tests only
pytest tests/integration/ -v                   # Integration tests only

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run linters
ruff check .                                   # Code quality
mypy src/ --ignore-missing-imports            # Type checking
pip-audit -r requirements.txt                 # Security scan
```

## Development

### Running Individual Components

```bash
# Stage 1: Scrapy crawler
scrapy crawl scout -a seed_file=test_seeds.txt -s CLOSESPIDER_PAGECOUNT=10

# Stage 2: Analysis worker
python -c "import asyncio; from src.stage2.stage2_worker import Stage2Worker; asyncio.run(Stage2Worker(max_concurrent=10).run())"

# Stage 3: Summarization worker
python -c "import asyncio; from src.stage3.stage3_worker import Stage3Worker; asyncio.run(Stage3Worker(max_concurrent=5).run())"

# Stage 4: Large document processor
python -m src.stage4.large_doc_processor
```

### Adding Custom Seed URLs

Create a text file with one URL per line:

```bash
# test_seeds.txt
https://example.com/page1
https://example.com/page2
https://example.com/page3
```

Then run:

```bash
python run_full_pipeline_test.py  # Uses test_seeds.txt by default
# OR
scrapy crawl scout -a seed_file=my_custom_seeds.txt
```

## CI/CD Pipeline

Automated GitHub Actions workflow includes:

1. **Linting** - Ruff + mypy type checking
2. **Security** - pip-audit vulnerability scanning
3. **Testing** - pytest with coverage reports
4. **Build** - Validates imports and directory structure
5. **Deploy** - Production deployment (on main branch)

## Troubleshooting

### Common Issues

**Import errors**
```bash
# Make sure you're running from project root
cd /path/to/Scraping_project
python run_pipeline.py
```

**Delta Lake errors**
```bash
# Ensure Delta Lake dependencies are installed
pip install deltalake>=1.1.0 pyarrow>=17.0.0
```

**Playwright errors**
```bash
# Install Playwright browsers
playwright install chromium
```

**Model download errors**
```bash
# If setup_models.py fails, you can manually download models:
python -c "
from transformers import pipeline
print('Downloading DistilBART...')
pipeline('summarization', model='sshleifer/distilbart-cnn-12-6', device=-1)
print('Downloading BART-large...')
pipeline('summarization', model='facebook/bart-large-cnn', device=-1)
print('Done!')
"
```

**Models not found during pipeline run**
```bash
# Ensure transformers and torch are installed
pip install transformers>=4.50.0 torch>=2.6.0

# Re-run model setup
python setup_models.py
```

**Non-HTML content crashes spider**
- Fixed in [src/stage1/scout_spider.py:101-127](src/stage1/scout_spider.py#L101-L127)
- Spider now checks Content-Type header and skips non-HTML content (PDFs, images, etc.)
```

**Memory issues with large documents**
```bash
# Reduce concurrent workers in Stage 2/3
# Edit max_concurrent in worker initialization
```

**No summaries generated**
```bash
# Check if quality documents exist
python -c "
from src.common.delta_lake import get_delta_manager
delta = get_delta_manager()
pages = delta.read('stage2_page_analysis')
quality = [p for p in pages if not p.get('is_low_quality')]
print(f'Quality docs: {len(quality)}')
"
```

### Logs

Check logs for detailed error messages:

```bash
# Pipeline logs
tail -f data/logs/pipeline.log

# Scrapy logs
tail -f data/logs/scrapy.log
```

### Clean Start

To reset all data and start fresh:

```bash
# WARNING: This deletes all Delta Lake data
rm -rf data/delta_lake/*
```

## Performance Tips

1. **Increase concurrency** for faster crawling (Stage 1):
   - Edit `CONCURRENT_REQUESTS` in `scout_spider.py`

2. **Batch processing** for efficiency (Stage 2/3):
   - Increase `batch_size` in worker initialization

3. **GPU acceleration** for summarization:
   - Change `device=-1` to `device=0` in summarization models
   - Requires CUDA-compatible GPU

4. **Reduce memory usage**:
   - Lower `max_concurrent` workers
   - Decrease `batch_size`
   - Process in smaller chunks

## Requirements

- **Python**: 3.10+
- **OS**: macOS, Linux, or Windows
- **Memory**: 4GB+ recommended
- **Storage**: Depends on crawl size (Delta Lake is efficient)
- **Optional**: CUDA GPU for faster summarization

See [requirements.txt](requirements.txt) for complete package list.

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Run tests (`pytest tests/`)
4. Commit changes (`git commit -m 'Add amazing feature'`)
5. Push to branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

## License

MIT License - See LICENSE file for details

## Support

- **Issues**: https://github.com/benjaminrussell/uconn-scraper/issues
- **Docs**: https://github.com/benjaminrussell/uconn-scraper/tree/main/docs
