# Packaging & dependency layers (#143)

## Sources of truth

| Layer | File | Role |
|-------|------|------|
| Installable package | `pyproject.toml` | `[project]` core deps, `optional-dependencies` (`ml`, `ocr`, `dev`, `stage3-4`), `requires-python >=3.11` |
| Core lock | `requirements.txt` (from `requirements.in`) | Lean pins for Docker/CI. No torch/OCR (#144) |
| ML / OCR locks | `requirements-ml.txt`, `requirements-ocr.txt` | Stage 4 / `ml` image only |
| Core intent | `requirements.in` | Core runtime pins; ML/OCR called out as extras (not default) |

## How to install

```bash
cd Scraping_project
pip install -e ".[dev]"          # core + tooling
pip install -e ".[ml,ocr]"       # Stage 4 heavy stacks (Stage 3 does not need these)
# or: pip install -e ".[stage3-4]"
# or: pip install -r requirements-ml.txt -r requirements-ocr.txt
python -m build && pip install dist/*.whl
python -c "import src"
```

## Docker runtime

See `docs/OPTIONAL_EXTRAS.md`.

- Lean Stage 1–3: targets `core` / `production` / `stage3` install `requirements.txt` only (no torch).
- Stage 4: target `stage4` (alias `ml`) installs `requirements-ml.txt` + `requirements-ocr.txt`. Compose profiles `ml` and `full`.
- `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` / `REDIS_DB` stay the Redis contract. Do not publish `REDIS_URL`.

## CI smoke

Workflow job `package` runs `python -m build`, installs the wheel, and `import src`.
