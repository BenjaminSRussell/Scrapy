# Stage 1 spiders: supported vs experimental (#391, #442)

| Spider | Module | Status | How to run |
|---|---|---|---|
| `scout` | `src/stage1/scout_spider.py` | **Supported** (compose, Helm, orchestrator) | `python cli.py scrapy` / `scrapy crawl scout` |
| `javascript` | `experimental/js_spider.py` | Experimental | opt in (below) |
| `deep_dive` | `experimental/deep_dive_spider.py` | Experimental | `python cli.py deep_dive --experimental` |
| `depth` | `experimental/depth_spider.py` | Experimental | opt in (below) |

`experimental/base_spider.py`, `playwright_guard.py` and `playwright_blocking.py`
are support modules, not spiders you run directly.

## Opting in

Experimental spiders import normally (CI smoke-imports them in
`tests/unit/stage1/test_experimental_spider_imports.py`), but they **refuse to
crawl** unless one of these is set:

- `ENABLE_EXPERIMENTAL_SPIDERS=1` in the environment (see `docs/FEATURE_FLAGS.md`)
- the Scrapy setting `ENABLE_EXPERIMENTAL_SPIDERS = True` (`-s ENABLE_EXPERIMENTAL_SPIDERS=1`)
- `python cli.py deep_dive --experimental` or `python cli.py scrapy --spiders javascript --experimental`

Without an opt-in, the CLI exits with status 2 and a message. A direct `scrapy crawl deep_dive`
fails in `from_crawler` with `ExperimentalSpiderDisabled`. Either way, an opted-in run still
logs a warning.

## Prerequisites

| Spider | Needs |
|---|---|
| `javascript` | `scrapy-playwright` and a Chromium build (`playwright install chromium`); Redis (`js_spider:priority_queue`); up to **12 GB RAM** (`MEMUSAGE_LIMIT_MB=12288`); cap renders with `PLAYWRIGHT_MAX_CONTEXTS` / `PLAYWRIGHT_MAX_PAGES_PER_CONTEXT` |
| `deep_dive` | Redis; about 4 GB RAM (`spider_config` `memory_limit_mb`) |
| `depth` | Redis; about 4 GB RAM; `stage1.depth_spider` config block |

## Graduating a spider

To graduate a spider:

1. Move it out of `experimental/`.
2. Drop `ExperimentalSpiderMixin`.
3. Add it to `SUPPORTED_SPIDERS` in `gate.py`.
4. Wire it into compose/Helm.
5. Add an end-to-end test.
