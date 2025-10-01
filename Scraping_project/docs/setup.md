# Setup Guide

This guide will help you set up the UConn Web Scraping Pipeline on your local machine.

## Prerequisites

### System Requirements

- **Python**: 3.10 or higher
- **Operating System**: macOS, Linux, or Windows (WSL recommended)
- **Memory**: Minimum 4GB RAM (8GB recommended for NLP features)
- **Disk Space**: At least 2GB free space

### Required Software

```bash
# Check Python version
python --version  # Should be 3.10+

# Install pip if not available
python -m ensurepip --upgrade
```

## Installation Methods

### Method 1: Quick Setup (Recommended)

Use the provided setup script for automated installation:

```bash
# Clone the repository
git clone https://github.com/benjaminrussell/uconn-scraper.git
cd uconn-scraper

# Run the setup script
./setup.sh
```

The setup script will:
1. Create a virtual environment
2. Install all dependencies
3. Download the spaCy language model
4. Set up pre-commit hooks (dev mode)

### Method 2: Manual Setup

#### 1. Create Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On macOS/Linux:
source venv/bin/activate

# On Windows:
venv\Scripts\activate
```

#### 2. Install the Package

**For users (minimal dependencies)**:
```bash
pip install -e .
python -m spacy download en_core_web_sm
```

**For developers (with testing tools)**:
```bash
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
pre-commit install
```

**With NLP extras (transformers)**:
```bash
pip install -e ".[nlp]"
```

**With everything**:
```bash
pip install -e ".[all]"
python -m spacy download en_core_web_sm
pre-commit install
```

### Method 3: Using Makefile

```bash
# Install core dependencies
make install

# Install with development tools
make install-dev

# Install with NLP extras
make install-nlp

# Install everything
make install-all
```

## Configuration

### 1. Environment Setup

The pipeline uses YAML configuration files in the `config/` directory:

```bash
# Copy example config (if available)
cp config/development.yml config/local.yml

# Or use the default development config
# No action needed - development.yml is the default
```

### 2. Configuration File Structure

Edit `config/development.yml` (or create your own):

```yaml
environment: development

# Scrapy settings
scrapy:
  concurrent_requests: 32
  download_delay: 0.1
  user_agent: "Your-Bot/1.0"

# Stage configurations
stages:
  discovery:
    max_depth: 3
    output_file: "data/processed/stage01/discovery_output.jsonl"
    seed_file: "data/raw/uconn_urls.csv"

  validation:
    max_workers: 16
    timeout: 15
    output_file: "data/processed/stage02/validation_output.jsonl"

  enrichment:
    nlp_enabled: true
    output_file: "data/processed/stage03/enrichment_output.jsonl"

# Data paths
data:
  raw_dir: "data/raw"
  processed_dir: "data/processed"
  logs_dir: "data/logs"
  temp_dir: "data/temp"

# Logging
logging:
  level: "INFO"
  structured: false  # Set true for JSON logs
```

### 3. Environment Variables (Optional)

Override configuration with environment variables:

```bash
# Set concurrent requests
export SCRAPY_CONCURRENT_REQUESTS=64

# Set discovery depth
export STAGE1_MAX_DEPTH=5

# Set validation workers
export STAGE2_MAX_WORKERS=32
```

## Data Setup

### 1. Prepare Seed URLs

Create your seed URL file:

```bash
# Create data directories
mkdir -p data/raw

# Add seed URLs (one per line, no header)
cat > data/raw/uconn_urls.csv << EOF
https://uconn.edu
https://www.uconn.edu/admissions
https://catalog.uconn.edu
EOF
```

### 2. Create Data Directories

```bash
# Create all required directories
for dir in data/raw data/processed/stage01 data/processed/stage02 data/processed/stage03 \
           data/cache data/logs data/temp data/checkpoints; do
    mkdir -p "$dir"
done
```

Or use make:
```bash
make clean  # Creates directories automatically
```

## Verify Installation

### 1. Check Package Installation

```bash
# Check if package is installed
pip list | grep uconn-scraper

# Verify command-line tool
uconn-scraper --help
```

### 2. Run Tests

```bash
# Run all tests
pytest

# Or use make
make test
```

### 3. Check Dependencies

```bash
# Verify spaCy model is installed
python -c "import spacy; nlp = spacy.load('en_core_web_sm'); print('spaCy OK')"

# Verify Scrapy
python -c "import scrapy; print(f'Scrapy {scrapy.__version__}')"

# Verify aiohttp
python -c "import aiohttp; print(f'aiohttp {aiohttp.__version__}')"
```

## Quick Start

### 1. Run a Simple Test

```bash
# Run Stage 1 discovery only
python -m src.orchestrator.main --stage 1

# Or use the CLI
uconn-scraper --stage 1

# Or use make
make run-stage1
```

### 2. Check Output

```bash
# View discovered URLs
head data/processed/stage01/new_urls.jsonl

# Check logs
tail -f data/logs/pipeline.log
```

## Common Issues

### Issue: ImportError for spaCy model

**Problem**: `Can't find model 'en_core_web_sm'`

**Solution**:
```bash
python -m spacy download en_core_web_sm
```

### Issue: Permission denied on setup.sh

**Problem**: `Permission denied: ./setup.sh`

**Solution**:
```bash
chmod +x setup.sh
./setup.sh
```

### Issue: Virtual environment not activated

**Problem**: Installing in wrong Python environment

**Solution**:
```bash
# Activate virtual environment first
source venv/bin/activate  # macOS/Linux
venv\Scripts\activate     # Windows
```

### Issue: Scrapy twisted.internet.error

**Problem**: Twisted reactor conflicts

**Solution**:
```bash
# Reinstall Twisted
pip uninstall twisted
pip install --no-cache-dir twisted
```

### Issue: Out of memory during NLP

**Problem**: Running out of RAM during enrichment

**Solution**:
1. Reduce batch size in config:
   ```yaml
   stages:
     enrichment:
       batch_size: 100  # Reduce from 1000
   ```

2. Disable transformer models:
   ```yaml
   nlp:
     transformer_model: null
   ```

## Advanced Setup

### Using Different Environments

```bash
# Development
uconn-scraper --env development --stage all

# Production (create production.yml first)
uconn-scraper --env production --stage all
```

### Custom Configuration File

```bash
# Create custom config
cp config/development.yml config/my-config.yml

# Edit as needed
nano config/my-config.yml

# Use it
uconn-scraper --env my-config --stage all
```

### Enable Structured Logging

Edit config:
```yaml
logging:
  structured: true  # Enable JSON logging
```

### GPU Acceleration (Optional)

For faster NLP with GPU:

```bash
# Install PyTorch with CUDA support
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118

# Install with NLP extras
pip install -e ".[nlp]"

# Configure to use GPU
export CUDA_VISIBLE_DEVICES=0
```

## Next Steps

- Read [usage.md](usage.md) for detailed command reference
- Review [architecture.md](architecture.md) to understand the system
- Check [development.md](development.md) if you want to contribute

## Getting Help

- Check logs in `data/logs/pipeline.log`
- Review [troubleshooting](#common-issues) section above
- Open an issue on GitHub
- Check existing documentation in `docs/`
