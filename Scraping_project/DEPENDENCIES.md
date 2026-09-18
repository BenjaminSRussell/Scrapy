### Installation (lean core vs full ML/OCR)

**Core (Stage 1 discovery + Stage 2 analysis — no torch):**
```bash
pip install -r requirements.txt
pip install -r dev-requirements.txt  # optional, for development
```

**Stage 3/4 / `ml_service` (ML + OCR extras):**
```bash
pip install -r requirements.txt -r requirements-ml.txt -r requirements-ocr.txt
# or, from Scraping_project/:
pip install -e ".[ml,ocr]"
# combined alias:
pip install -e ".[stage3-4]"
```

| Extra | Packages (direct) | Needed by |
|-------|-------------------|-----------|
| *(core)* | Scrapy, deltalake/pyarrow, redis, httpx/aiohttp, prometheus-client, pydantic, confluent-kafka, … | `scraper`, `stage1-worker`, `stage2-worker` |
| `ml` | torch, transformers, sentence-transformers, scikit-learn | `stage3-worker`, `stage4-worker`, `src/ml_service.py` |
| `ocr` | easyocr, pdf2image, PyPDF2, pillow | `stage4-worker` (PDF/image docs) |
| `stage3-4` | `ml` + `ocr` | Stage 3/4 images |

**Docker Compose profiles:**
```bash
# Lean: redis + scraper + Stage 1/2 + monitoring (core images, no torch)
docker compose up -d

# Full: also start Stage 3/4 (ML/OCR image targets)
docker compose --profile ml up -d
# alias:
docker compose --profile full up -d
```

Stage 3 and Stage 4 **fail fast** at startup if ML (and for Stage 4, OCR) extras are missing, with install hints pointing at the commands above.

**Image size:** Stage 1/2 / `production` targets install core only. Expect roughly **2–4 GB** smaller images vs the previous monolithic `requirements.txt` (torch + transformers + easyocr trees). Confirm locally with `docker images` before/after rebuild.
