## Optional ML/OCR extras (lean images)

See also [DEPENDENCIES.md](../DEPENDENCIES.md).

Docker build targets (`Dockerfile`):

| Target | Installs | Use for |
|--------|----------|----------|
| `base` / `core` / `production` | `requirements.txt` only | scraper, Stage 1, Stage 2, **light Stage 3** |
| `stage3` | same as `core` (datasketch already in core) | Stage 3 MinHash dedupe + extractive summary |
| `ml` / `stage4` | core + `requirements-ml.txt` + `requirements-ocr.txt` | Stage 4, `ml_service` |

Compose: default `docker compose up` is lean (includes Stage 3 on the core image);
add `--profile ml` (or `full`) for Stage 4 (ML/OCR image).

Stage worker commands use `#262` entrypoint shims:
`python -m src.workers.stage{1,2,3,4}_worker`.

`pyproject.toml` optional-deps are owned by **PR #289** — this PR keeps tooling-only
`pyproject.toml` and installs extras via `requirements-*.txt`.

### Image size (AC4)

**Measurement status (2026-09-18 ET):** Docker is **not available** in the review
environment (`docker: command not found`). No invented sizes are recorded here.

**Measure locally after rebuild:**
```bash
cd Scraping_project
docker build -t scrapy-core:local --target core .
docker build -t scrapy-ml:local --target ml .
# stage3 is an alias of core (light path); stage4 aliases ml
docker build -t scrapy-stage3:local --target stage3 .
docker build -t scrapy-stage4:local --target stage4 .
docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' \
  scrapy-core:local scrapy-ml:local scrapy-stage3:local scrapy-stage4:local
```

**Expected qualitative delta:** `core` / `stage3` should be roughly **2–4 GB**
smaller than `ml` / `stage4` because torch + transformers + easyocr (and
transitive CUDA/CPU wheels) stay out of the lean image. Confirm with the
commands above and paste `docker images` output into the PR when measured.
