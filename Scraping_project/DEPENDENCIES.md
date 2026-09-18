### Installation (lean core vs full ML/OCR)

**Core (Stage 1 discovery + Stage 2 analysis + light Stage 3 — no torch):**
```bash
pip install -r requirements.txt
pip install -r dev-requirements.txt  # optional, for development
```

Light Stage 3 uses **datasketch** (already in the core lock) for MinHash
deduplication plus extractive sentence truncation — it does **not** need
`requirements-ml.txt`.

**Stage 4 / `ml_service` (ML + OCR extras):**
```bash
pip install -r requirements.txt -r requirements-ml.txt -r requirements-ocr.txt
```

Packaging extras (`pip install -e ".[ml,ocr]"` / `.[stage3-4]`) are owned by
**PR #289** (complete `pyproject.toml`). Prefer `requirements-*.txt` from this
issue (#144) until that packaging PR lands — do not rely on a partial
`[project.optional-dependencies]` table in this branch.

| Extra | Packages (direct) | Needed by |
|-------|-------------------|-----------|
| *(core)* | Scrapy, deltalake/pyarrow, redis, httpx/aiohttp, prometheus-client, pydantic, confluent-kafka, **datasketch**, … | `scraper`, `stage1-worker`, `stage2-worker`, `stage3-worker` (light) |
| `ml` (`requirements-ml.txt`) | torch, transformers, sentence-transformers, scikit-learn | `stage4-worker`, `src/ml_service.py` |
| `ocr` (`requirements-ocr.txt`) | easyocr, pdf2image, PyPDF2, pillow | `stage4-worker` (PDF/image docs) |

**Recompile locks (pip-tools / Makefile):**
```bash
make update-deps
# or:
pip-compile --output-file=requirements.txt requirements.in
pip-compile --output-file=requirements-ml.txt requirements-ml.in
pip-compile --output-file=requirements-ocr.txt requirements-ocr.in
```

**Docker Compose profiles:**
```bash
# Lean: redis + scraper + Stage 1/2/3 + monitoring (core images, no torch)
docker compose up -d

# Full: also start Stage 4 (ML/OCR image target)
docker compose --profile ml up -d
# alias:
docker compose --profile full up -d
```

Stage 3 **fail-fast** checks for **datasketch** (core) only — not torch.
Stage 4 **fail-fast** at startup if ML and OCR extras are missing, with install
hints pointing at the commands above.

Worker entrypoints use `python -m src.workers.stageN_worker` (#262 shims).

### Image size (AC4)

**Measurement blocked in this environment (2026-09-18 ET):** `docker` is not
installed (`command not found`). Do **not** treat any prior ~2–4 GB figure as a
measured value — it is qualitative only.

**Commands to record real sizes locally:**
```bash
cd Scraping_project
docker build -t scrapy-core:local --target core .
docker build -t scrapy-ml:local --target ml .
docker build -t scrapy-stage3:local --target stage3 .
docker build -t scrapy-stage4:local --target stage4 .
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' \
  scrapy-core:local scrapy-ml:local scrapy-stage3:local scrapy-stage4:local
```

**Expected qualitative delta:** `core`/`stage3` omit torch + transformers +
easyocr trees, so they should land roughly **2–4 GB** under `ml`/`stage4`.
Paste measured `docker images` output into the PR when available.
