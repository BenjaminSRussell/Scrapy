# Complete Setup Guide

This guide walks you through setting up the UConn Web Scraping Pipeline from scratch.

## Prerequisites

- **Python 3.10+** (Python 3.13 recommended)
- **4GB+ RAM** recommended
- **~5GB disk space** (2GB for models, rest for data)
- **Internet connection** for model downloads

## Step-by-Step Setup

### 1. Clone and Create Virtual Environment

```bash
# Clone repository
git clone https://github.com/benjaminrussell/uconn-scraper.git
cd uconn-scraper

# Create virtual environment
python -m venv .venv

# Activate virtual environment
source .venv/bin/activate  # macOS/Linux
# OR
.venv\Scripts\activate     # Windows
```

### 2. Install Dependencies

```bash
# Upgrade pip
pip install --upgrade pip

# Install all dependencies
pip install -r requirements.txt

# Verify no conflicts
pip check
```

**Expected output**: `No broken requirements found.`

### 3. Install Playwright Browsers

```bash
# Install Chromium for JS-heavy pages
playwright install chromium
```

This downloads ~150MB and enables JavaScript rendering for single-page applications.

### 4. Download Transformer Models (REQUIRED)

```bash
# Run model setup script
python setup_models.py
```

This will:
1. Download **DistilBART** (~350 MB) for Stage 3 fast summarization
2. Download **BART-large** (~1.6 GB) for Stage 4 large document processing
3. Cache models in `~/.cache/huggingface/hub/`

**⏱️ Time**: 5-15 minutes depending on internet speed

**⚠️ Important**: Do NOT skip this step. The pipeline will fail without these models.

### 5. Verify Installation

```bash
# Run health check
python health_check.py
```

**Expected output**:
- ✅ Directory structure OK
- ✅ Dependencies installed
- ✅ Delta Lake accessible
- Summary of current data state

## First Run - Quick Test

**Recommended**: Start with a limited test run before processing large datasets.

```bash
# Run pipeline on 5 test pages
python run_full_pipeline_test.py
```

**What this does**:
1. Crawls 5 pages from `test_seeds.txt`
2. Analyzes content quality
3. Generates summaries
4. Displays results summary

**Expected output**:
```
Stage 1 (Discovery): 5 URLs discovered
Stage 2 (Analysis): 5 pages analyzed
  Quality documents: 3-5
Stage 3 (Summaries): 3-5 summaries created
```

**⏱️ Time**: 1-3 minutes

## Full Pipeline Run

After verifying the test run works:

```bash
# Run complete pipeline
python run_pipeline.py
```

This will:
1. Load seed URLs from `data/raw/uconn_urls.csv`
2. Crawl with full depth and concurrency
3. Process all stages
4. Save to Delta Lake tables

## Troubleshooting Setup

### Import Errors

```bash
# Make sure you're in project root
pwd  # Should show: .../Scraping_project

# Ensure virtual environment is activated
which python  # Should show: .../Scraping_project/.venv/bin/python
```

### Model Download Fails

**Issue**: `setup_models.py` fails or times out

**Solutions**:

1. **Manual download**:
   ```bash
   python -c "
   from transformers import pipeline
   print('Downloading DistilBART...')
   pipeline('summarization', model='sshleifer/distilbart-cnn-12-6', device=-1)
   print('Downloading BART-large...')
   pipeline('summarization', model='facebook/bart-large-cnn', device=-1)
   print('Done!')
   "
   ```

2. **Check internet connection**:
   ```bash
   curl -I https://huggingface.co
   ```

3. **Set HuggingFace cache location** (if disk space limited):
   ```bash
   export HF_HOME=/path/to/large/disk
   python setup_models.py
   ```

### Delta Lake Errors

**Issue**: `No module named 'deltalake'`

```bash
# Ensure Delta Lake is installed
pip install deltalake>=1.1.0 pyarrow>=17.0.0

# Verify
python -c "import deltalake; print('OK')"
```

### Playwright Errors

**Issue**: `Browser not found`

```bash
# Re-install browsers
playwright install chromium

# Verify
playwright install --dry-run
```

### Memory Issues

**Issue**: Out of memory during model loading

**Solutions**:

1. **Reduce concurrent workers** in `run_pipeline.py`:
   ```python
   # Stage 2
   worker = Stage2Worker(max_concurrent=5)  # Default: 10

   # Stage 3
   worker = Stage3Worker(max_concurrent=3)  # Default: 20
   ```

2. **Process in batches**:
   ```bash
   # Limit pages per run
   scrapy crawl scout -s CLOSESPIDER_PAGECOUNT=100
   ```

## Verifying Everything Works

### 1. Check Dependencies

```bash
pip list | grep -E "(scrapy|delta|playwright|transformers|torch)"
```

**Expected**:
- scrapy ~2.13.3
- deltalake ~1.1.0+
- playwright ~1.49.0+
- transformers ~4.50.0+
- torch ~2.6.0+

### 2. Check Models

```bash
ls ~/.cache/huggingface/hub/
```

**Expected**: Directories for both models:
- `models--sshleifer--distilbart-cnn-12-6`
- `models--facebook--bart-large-cnn`

### 3. Run Tests

```bash
pytest tests/ -v
```

**Expected**: All tests pass (29+ tests)

### 4. Check Data Directories

```bash
ls -la data/
```

**Expected structure**:
```
data/
├── delta_lake/     # Created on first run
├── logs/           # Pipeline logs
├── cache/          # Temp cache
└── raw/            # Seed files
```

## Next Steps

1. **Customize seed URLs** in `test_seeds.txt` or `data/raw/uconn_urls.csv`
2. **Adjust settings** in pipeline scripts (concurrency, depth, quality thresholds)
3. **Run full pipeline** with `python run_pipeline.py`
4. **Query results** from Delta Lake tables

## Configuration Quick Reference

### Stage 1: Crawler Settings
File: `src/stage1/scout_spider.py`
```python
CONCURRENT_REQUESTS = 256      # Parallel requests
DEPTH_LIMIT = 3               # Max crawl depth
CLOSESPIDER_PAGECOUNT = 1000  # Stop after N pages
```

### Stage 2: Quality Thresholds
File: `src/stage2/intelligent_analyzer.py`
```python
MIN_WORD_COUNT = 50           # Minimum words
MIN_TEXT_TO_HTML_RATIO = 0.1  # Min content ratio
MASSIVE_DOC_THRESHOLD = 50000 # Characters for Stage 4
```

### Stage 3: Summarization
File: `src/stage3/stage3_worker.py`
```python
max_concurrent = 20           # Concurrent workers
batch_size = 50              # Documents per batch
SIMILARITY_THRESHOLD = 0.3    # Deduplication threshold
```

## Getting Help

- **GitHub Issues**: https://github.com/benjaminrussell/uconn-scraper/issues
- **Documentation**: See [README.md](README.md)
- **Logs**: Check `data/logs/pipeline.log` for errors

## Common Setup Mistakes

❌ **Don't**: Skip `setup_models.py` - models are required
✅ **Do**: Run model setup before first pipeline run

❌ **Don't**: Run from subdirectories
✅ **Do**: Always run from project root

❌ **Don't**: Use system Python
✅ **Do**: Use virtual environment

❌ **Don't**: Install one package at a time
✅ **Do**: Use `pip install -r requirements.txt`

## Estimated Setup Time

| Step | Time |
|------|------|
| Clone & venv | 1 min |
| Install dependencies | 2-5 min |
| Install Playwright | 1-2 min |
| Download models | 5-15 min |
| **Total** | **10-25 min** |

---

**You're ready!** Run `python run_full_pipeline_test.py` to start scraping.
