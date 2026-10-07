## Summary
<!-- What does this change? Why? -->

## Checklist
- [ ] Tests pass locally (`cd Scraping_project && pytest -q -o addopts= -m "not slow and not kafka and not performance"`)
- [ ] Lint passes (`cd Scraping_project && ruff check src/ --select F,E4,E7,E9`)
- [ ] Types pass (`cd Scraping_project && mypy src/ --config-file mypy.ini --ignore-missing-imports --no-strict-optional`)
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
