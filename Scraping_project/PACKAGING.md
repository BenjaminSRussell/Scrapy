# Packaging & dependency layers (#143)

## Sources of truth

| Layer | File | Role |
|-------|------|------|
| Installable package | `pyproject.toml` | `[project]` core deps, `optional-dependencies` (`ml`, `ocr`, `dev`, `stage3-4`), `requires-python >=3.11` |
| Historical lock | `requirements.txt` (from `requirements.in`) | Full pin set still used by some Docker/CI paths |
| Core intent | `requirements.in` | Core runtime pins; ML/OCR called out as extras (not default) |

## How to install

```bash
cd Scraping_project
pip install -e ".[dev]"          # core + tooling
pip install -e ".[ml,ocr]"       # Stage 3/4 heavy stacks
# or: pip install -e ".[stage3-4]"
python -m build && pip install dist/*.whl
python -c "import src"
```

## Docker runtime

- Lean / Stage 1–2: core only (no torch) — see #144 / `DOCKER_EXTRAS.md` when present.
- Stage 3–4: install `.[ml,ocr]` or build target `full`.

Until #144 lands on main, `Dockerfile` may still `pip install -r requirements.txt` (full lock). Prefer aligning images to `pyproject` extras after that.

## CI smoke

Workflow job `package` runs `python -m build`, installs the wheel, and `import src`.
