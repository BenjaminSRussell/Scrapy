# UConn Web Scraping Pipeline

A simple web scraping pipeline for uconn.edu that discovers URLs, validates them, and extracts content.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt
python -m spacy download en_core_web_sm

# Run the pipeline
python main.py --env development --stage all
```

## How It Works

The pipeline has 3 stages:

1. **Discovery** - Finds new URLs from seed file and sitemap
2. **Validation** - Checks if URLs are accessible
3. **Enrichment** - Extracts content and metadata

## Project Structure

```
Scraping_project/
├── main.py              # Main entry point
├── src/                 # Core pipeline code
│   ├── stage1/          # URL discovery
│   ├── stage2/          # URL validation
│   ├── stage3/          # Content enrichment
│   ├── common/          # Shared utilities
│   └── orchestrator/    # Pipeline coordination
├── tools/               # Utility scripts
├── tests/               # Test suite
├── config/              # Configuration files
└── data/                # Input/output data
```

## Configuration

- `config/development.yml` - Development settings
- `config/production.yml` - Production settings
- `data/raw/uconn_urls.csv` - Input seed URLs

## Running Stages

```bash
# Run individual stages
python main.py --stage 1  # Discovery only
python main.py --stage 2  # Validation only
python main.py --stage 3  # Enrichment only

# Run all stages
python main.py --stage all
```

## Testing

```bash
python -m pytest
```

## Branching and Development

### Creating a New Branch

```bash
# Create and switch to a new feature branch
git checkout -b feature/your-feature-name

# Make your changes
git add .
git commit -m "Add your feature"

# Push to remote
git push -u origin feature/your-feature-name
```

### Contributing

1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes
4. Run tests to ensure everything works
5. Submit a pull request

### Branch Naming

- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation updates
- `refactor/` - Code improvements

## Issues and Future Problems

### Current Known Issues

1. **Stage 3 CLI Bug** - Stage 3 doesn't work through orchestrator, must run directly via Scrapy
2. **Memory Usage** - Large crawls can consume significant memory due to in-memory URL deduplication
3. **Error Handling** - Some network errors cause the entire stage to fail instead of continuing
4. **Configuration Complexity** - Too many configuration options make setup confusing

### Future Improvements Needed

1. **Persistence** - Add database storage to replace JSONL files for better performance
2. **Resumability** - Allow restarting from checkpoints after failures
3. **Rate Limiting** - Implement proper rate limiting to avoid overwhelming target servers
4. **Monitoring** - Add real-time progress tracking and metrics dashboard
5. **Docker Support** - Container-based deployment for easier setup
6. **Data Quality** - Better content filtering and duplicate detection
7. **API Integration** - REST API for external systems to query scraped data
8. **Scalability** - Distributed processing for large-scale crawls

### Technical Debt

- Remove remaining try/except blocks that hide important errors
- Simplify configuration system - too many overlapping options
- Consolidate duplicate test files and improve test coverage
- Standardize logging format across all modules
- Remove unused dependencies from requirements.txt