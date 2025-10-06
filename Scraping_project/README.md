# Ultra Scraping Pipeline

Mass web scraping with **ultra-aggressive URL discovery**, **media processing** (OCR + Whisper), and **Delta Lake** storage.

## Quick Start

```bash
# Install
pip install -r requirements.txt
brew install ffmpeg  # macOS (or apt-get on Linux)

# Run
python run_pipeline.py
```

## Architecture

```
Stage 1: Ultra Discovery
  ├── Finds ALL URLs (standard + hidden + obfuscated)
  ├── Detects media (images, audio, video)
  ├── Updates seed file with new URLs
  └── Saves to Delta Lake

Stage 3: Enrichment
  ├── Extracts text content
  ├── OCR on images
  ├── Whisper transcription on audio/video
  ├── YAKE keyword extraction
  ├── DeBerta classification
  └── Saves enriched data to Delta Lake
```

## Usage

```bash
# Full pipeline (unlimited depth)
python run_pipeline.py --stage all

# Discovery only
python run_pipeline.py --stage discovery --max-depth 5

# Enrichment only
python run_pipeline.py --stage enrichment

# Custom seed file
python run_pipeline.py --seed my_urls.csv
```

## Continuous Crawling

After each run, new URLs are automatically added to seed file. Run again to discover more!

```bash
python run_pipeline.py  # Discovers new URLs
python run_pipeline.py  # Discovers even more!
```

## File Structure

```
├── run_pipeline.py        # Main runner
├── src/
│   ├── common/           # 5 essential files
│   ├── stage1/           # Ultra discovery
│   └── stage3/           # Enrichment + media
├── data/
│   ├── delta_lake/       # All data here
│   └── raw/uconn_urls.csv  # Auto-updated!
└── config/settings.yml   # Configuration
```

## Data Access

```python
from src.common.delta_storage import get_storage

storage = get_storage()
data = storage.read()
print(f"Discovered {len(data)} records")
```

## Configuration

Edit `config/settings.yml` to control:
- Max crawl depth
- Media processing limits
- OCR/Whisper models
- Concurrent requests

## Requirements

- Python 3.10+
- FFmpeg (for video processing)
- See requirements.txt for packages

## Troubleshooting

**Import errors:** Run from project root  
**Media fails:** Install ffmpeg  
**Delta Lake errors:** `pip install deltalake pyarrow`  

Check logs: `data/logs/pipeline.log`
