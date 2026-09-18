## Optional ML/OCR extras (lean images)

See also [DEPENDENCIES.md](../DEPENDENCIES.md).

Docker build targets (`Dockerfile`):

| Target | Installs | Use for |
|--------|----------|----------|
| `base` / `core` / `production` | `requirements.txt` only | scraper, Stage 1, Stage 2 |
| `ml` / `stage3` / `stage4` | core + `requirements-ml.txt` + `requirements-ocr.txt` | Stage 3, Stage 4 |

Compose: default `docker compose up` is lean; add `--profile ml` (or `full`) for Stage 3/4.

Stage worker commands use `#262` entrypoint shims:
`python -m src.workers.stage{1,2,3,4}_worker`.

`pyproject.toml` optional-deps are owned by **PR #289** — this PR keeps tooling-only
`pyproject.toml` and installs extras via `requirements-*.txt`.

Expected Stage 1/2 image reduction vs the old monolithic requirements: **~2–4 GB**
(torch + transformers + easyocr and transitive wheels). Measure with `docker images`
after rebuild.
