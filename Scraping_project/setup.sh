#!/bin/bash
# UConn Web Scraping Pipeline Setup Script

set -e  # Exit on any error

echo "🚀 Setting up UConn Web Scraping Pipeline..."

# Change to the project directory
cd Scraping_project

# Check Python version
python3_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "📍 Python version: $python3_version"

if [[ $(python3 -c "import sys; print(sys.version_info >= (3, 9))") == "False" ]]; then
    echo "❌ Python 3.9+ required. Current version: $python3_version"
    exit 1
fi

echo "✅ Python version compatible"

# Create and activate virtual environment
if [ ! -d ".venv" ]; then
    echo "🐍 Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate
echo "✅ Virtual environment activated"

# Install pip dependencies
echo "📦 Installing Python dependencies..."
pip3 install -r requirements.txt

# Install spaCy language model
echo "🧠 Installing spaCy language model..."
python3 -m spacy download en_core_web_sm

# Create data directories
echo "📁 Creating data directories..."
mkdir -p data/{raw,processed/{stage01,stage02,stage03},logs,cache,exports,temp,checkpoints}

# Verify installation
echo "🔍 Verifying installation..."
python3 -c "
import spacy
nlp = spacy.load('en_core_web_sm')
print('✅ SpaCy model loaded successfully')

from src.common.schemas import DiscoveryItem, ValidationResult, EnrichmentItem
print('✅ Schema imports working')

from src.stage1.discovery_spider import DiscoverySpider
print('✅ Stage 1 imports working')

from src.stage2.validator import URLValidator
print('✅ Stage 2 imports working')

from src.stage3.enrichment_spider import EnrichmentSpider
print('✅ Stage 3 imports working')
"

echo ""
echo "🎉 Setup complete! You can now run the pipeline:"
echo ""
echo "Individual stages:"
echo "  scrapy crawl discovery"
echo "  python3 -m src.stage2.validator"
echo "  scrapy crawl enrichment -a urls_file=data/processed/stage02/validation_output.jsonl"
echo ""
echo "Orchestrator mode:"
echo "  python3 main.py --env development --stage 2"
echo "  python3 main.py --env development --stage 3"
echo ""
echo "Run tests:"
echo "  python3 -m pytest"
echo ""