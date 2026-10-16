# Ballpark crawl minutes and relative cost units before a start

- **Status:** Implemented as a standalone planning calculation (`python -m src.common.crawl_estimate`). Not wired into `start.py`, `cli.py`, Control Center, or any worker. Rates are placeholders.
- **Date:** 2026-10-16
- **Issue:** [#580](https://github.com/BenjaminSRussell/Scrapy/issues/580)

Ops asked for a rough figure before a crawl starts: how many minutes, and how much relative cost, for N URLs split across the spiders they plan to run. This file records the method, the placeholder rates, and the limits.

## Non-billing

The estimate is not a bill. It reads no price list, calls no provider API, opens no socket, and starts no spider. Cost units are relative (one scout worker-minute = 1) and are not currency. The existing FinOps proxy, `pipeline_estimated_cost_usd` (#545), counts scheduled requests times `COST_USD_PER_REQUEST`. It is also a proxy and not a cloud bill.

## Usage

From `Scraping_project/`:

```bash
python -m src.common.crawl_estimate --urls 10000 --mix scout=0.8,javascript=0.2
```

Output with the placeholder rates in this change:

```text
Crawl estimate for 10,000 URLs (one worker per spider, run in series)
  spider         share        URLs   minutes  cost units
  scout          80.0%       8,000        80          80
  javascript     20.0%       2,000       100         200
  total                                  180         280
Ballpark: ~180 minutes and ~280 relative cost units.
Non-billing. This is a ballpark planning figure, not a cloud, provider, or invoice amount. Cost units are relative (one scout worker-minute = 1) and are not currency. Rates are placeholder assumptions, not measurements.
```

Inputs:

- `--urls`: URLs to crawl. A whole number, 0 or more.
- `--mix`: `name=share` pairs separated by commas. Shares are fractions that must sum to 1. Default `scout=1`. Names: `scout`, `depth`, `deep_dive`, `javascript`. `base` is an experimental base class, not a mix option.
- `depth`, `deep_dive`, and `javascript` are experimental and need `ENABLE_EXPERIMENTAL_SPIDERS` to run ([`gate.py`](../../Scraping_project/src/stage1/experimental/gate.py)). The estimate does not check that flag.

## Method

For each spider `s` with share `p_s`, using its profile:

- URLs_s = URLs x p_s
- minutes_s = URLs_s / urls_per_minute_s
- cost_units_s = minutes_s x worker_weight_s

Totals add the spiders. That assumes one worker per spider, run one after another. Ops who run several workers on one spider can divide that spider's minutes by the worker count for a rough wall-clock figure. Cost units stay the same, since they count worker-minutes.

## Placeholder rates

Not measured. These values only make the arithmetic run. Do not quote them as measurements.

| Spider | URLs per minute (one worker) | Worker weight (cost units per minute) |
|---|---|---|
| `scout` | 100 | 1.0 |
| `depth` | 100 | 1.0 |
| `deep_dive` | 100 | 1.0 |
| `javascript` | 20 | 2.0 |

Basis:

- "Current throughput: ~100-200 URLs/minute" is stated as the current state in [`PHASE_8_STRATEGY.md`](../../Scraping_project/PHASE_8_STRATEGY.md) and repeated in [`EVOLUTION_ROADMAP.md`](../../Scraping_project/EVOLUTION_ROADMAP.md). Both are strategy notes, not benchmark results. `scout` takes the low end.
- [`config.yml`](../../Scraping_project/config.yml) sets `scout` to 1024 concurrent requests, 16 per host, and a 0.01 s download delay. Those are politeness and concurrency limits, not a measured rate. Real throughput depends on how many hosts a run touches and how long responses take, so the placeholder is not derived from them.
- `depth` and `deep_dive` are plain HTTP spiders on the base spider, with no Playwright handler, so they reuse the `scout` rate and weight.
- `javascript` runs the Playwright download handler in [`js_spider.py`](../../Scraping_project/src/stage1/experimental/js_spider.py). `gate.py` lists its prerequisite as Playwright and Chromium, "up to 12 GB RAM". A headless browser is slower and heavier per URL, so the table gives it one fifth of the rate and twice the weight. Per URL, `javascript` costs 10 times `scout` in this table. The memory figures are ceilings, not usage, so they did not set the weights. Both numbers are guesses.

## Calibrate before quoting a figure

Time a fixed URL set on the target host for each spider you plan to run. Set `urls_per_minute` to URLs divided by minutes. Set `worker_weight` from the instance price ratio, with `scout` at 1.0. Change `SPIDER_PROFILES` in [`crawl_estimate.py`](../../Scraping_project/src/common/crawl_estimate.py) and this table in the same change. Until then, treat the output as the shape of the work, not a forecast.

## Acceptance criteria

| Criterion | Where it is met |
|---|---|
| Estimate from spider mix x URL count | `estimate_crawl()`, with `--urls` and `--mix` on the command line |
| Ballpark minutes and relative cost units | the `Ballpark: ~N minutes and ~M relative cost units.` line, and `CrawlEstimate.minutes` and `.cost_units` |
| Non-billing disclaimer | `DISCLAIMER`, printed on every run and returned in `CrawlEstimate.disclaimer` |

Tests: [`test_crawl_estimate.py`](../../Scraping_project/tests/unit/common/test_crawl_estimate.py).

## Not in scope

- Dollar figures, provider or cloud price lists, or any billing feed.
- Wiring into `start.py`, `cli.py`, the dashboard, or `config.yml`. The command runs on its own.
- Any crawl, network call, Redis read, or Delta read.
- A benchmarks table in the root README. The issue lists one, but the README has none, and no measured numbers exist to put in it. The README gets only the command line under Common commands.
