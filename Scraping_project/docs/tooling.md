# Tooling: canonical config files (#360)

Each tool has exactly one config file. If you change a setting, change it there and nowhere else.

| Tool | Canonical file | Notes |
|---|---|---|
| ruff (lint, import sorting via rule `I`, formatting) | `ruff.toml` | `line-length = 120`, `target-version = "py311"`. ruff ignores `[tool.ruff]` in `pyproject.toml` when `ruff.toml` exists, so that section was removed. |
| mypy | `mypy.ini` | `python_version = 3.11`. CI adds `--ignore-missing-imports --no-strict-optional` on the command line. |
| pytest | `pytest.ini` | Markers, `pythonpath = . src`. CI clears `addopts` (`-o addopts=`). |
| pre-commit | `.pre-commit-config.yaml` | ruff + ruff-format, file hygiene, mypy. |
| bandit | CLI flags only | `bandit -r src/ -ll` (medium and high severity block CI). |
| CI matrix | `.github/workflows/main.yml` | Python 3.11 and 3.12. `noxfile.py` mirrors it, enforced by `tests/unit/test_contributor_tooling.py`. |

## Versions are aligned

- **Minimum Python is 3.11:** ruff `target-version`, mypy `python_version`, and the oldest CI matrix entry. CI also tests 3.12.
- **Line length is 120** (ruff lint and ruff format).

## One formatter path (#357)

- `ruff check --fix` handles lint and import order; `ruff format` handles formatting (Black-compatible). Black and isort are **not** used. Running all three made hooks rewrite each other's output.
- `make lint` / `make lint-fix` and the pre-commit hooks run the same two commands.
- CI's blocking lint gate is narrower: `ruff check src/ --select F,E4,E7,E9`.
