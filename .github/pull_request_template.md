## Summary
<!-- What does this change? Why? Put `Closes #N` for each issue it fixes. -->

## What I ran
<!-- Paste the commands and the pytest summary line. CI runs the same default selection. -->
- [ ] Fast check: `cd Scraping_project && make test-unit` (or `pytest tests/unit -q -o addopts=`)
- [ ] CI default set: `cd Scraping_project && pytest -q -o addopts= -m "not slow and not kafka and not performance"`
- [ ] Markers I used or added (e.g. `unit`, `stage2`, `delta`, `slow`): <!-- list them -->
- [ ] Lint: `cd Scraping_project && ruff check src/ --select F,E4,E7,E9`
- [ ] Types: `cd Scraping_project && mypy src/ --config-file mypy.ini --ignore-missing-imports --no-strict-optional`

## Tests
- [ ] New or changed code has tests (a bug fix includes a test that fails without the fix)
- [ ] Tests run offline: no live network, Redis, Kafka or Postgres unless marked `redis`/`kafka`/`postgres`/`integration`
- [ ] Updated fixtures / contract files if the data shape changed (`tests/fixtures/`, `tests/contract/`)

## Compatibility
- [ ] Updated docs / comments (if behavior changed)
- [ ] Backwards compatible (or migration noted)

## Area labels (pick)
- [ ] area/stage1
- [ ] area/stage2
- [ ] area/stage3
- [ ] area/common
- [ ] area/integration
- [ ] area/nlp
- [ ] area/ci
