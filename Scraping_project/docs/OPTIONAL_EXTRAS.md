# Optional ML/OCR extras (#144)

Stage 1, Stage 2, and light Stage 3 (datasketch MinHash + extractive summary)
run on the core lock. Torch, transformers, sentence-transformers, scikit-learn,
easyocr, pdf2image, PyPDF2, and pillow are not in `requirements.txt`.

## Install

```bash
cd Scraping_project
pip install -r requirements.txt                      # lean / Stage 1–3
pip install -r requirements-ml.txt -r requirements-ocr.txt   # Stage 4
# packaging extras (pyproject.toml):
pip install -e ".[ml]"          # or ".[ocr]" or ".[stage3-4]"
```

Recompile locks from this directory (no machine-local paths):

```bash
make lock          # keep existing pins
make update-deps   # upgrade pins, including ML/OCR locks
```

## Docker targets

| Target | Contents | Compose |
|--------|----------|---------|
| `base` / `core` / `production` / `crawler` / `stage3` | core only | profile `core` (`scraper`, stage1–3) |
| `ml` / `stage4` | core + `requirements-ml.txt` + `requirements-ocr.txt` + poppler-utils | profiles `ml` or `full` (`stage4-worker`) |
| `playwright` | core + Playwright Chromium (not torch) | not in Compose; `docker build --target playwright` ([JS_RENDERING.md](JS_RENDERING.md)) |

```bash
docker compose --profile core build
docker compose --profile ml build stage4
docker compose --profile core up -d          # lean: no stage4
docker compose --profile ml up -d stage4-worker
```

Redis in Compose and Helm stays `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` /
`REDIS_DB`. Do not publish `REDIS_URL` (fallback only when `REDIS_HOST` is unset).

## Image size (AC4)

Not measured in this change (no image build recorded). Compare after a local rebuild:

```bash
docker build -t scrapy-core:local --target core .
docker build -t scrapy-stage3:local --target stage3 .
docker build -t scrapy-ml:local --target ml .
docker build -t scrapy-stage4:local --target stage4 .
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' \
  scrapy-core:local scrapy-stage3:local scrapy-ml:local scrapy-stage4:local
```

Qualitative expectation only: `core` / `stage3` omit the torch + transformers + easyocr trees (often on the order of a few GB). Paste real `docker images` output before treating AC4 as done.
