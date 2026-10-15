# JS spider debug pane: console sample, verbose toggle, slow-render warning

- **Status:** Design only. No listener, Redis key, dashboard route, config key, metric, or stat is added here.
- **Date:** 2026-10-15
- **Issue:** [#928](https://github.com/BenjaminSRussell/Scrapy/issues/928)

Issue #928 asks for a Chrome DevTools-style debug pane for the `javascript` spider. The acceptance criteria are: show a console error sample, toggle verbose output, and warn on slow renders. This file designs each one against the code on `main`. It changes no code. The pane itself belongs to the Control Center dashboard, which this chain does not touch.

## What ships today

- **No console listener.** `JavaScriptSpider.parse` attaches one page listener, `page.on("response", ...)` ([`js_spider.py`](../../Scraping_project/src/stage1/experimental/js_spider.py), line 193), to collect JSON API URLs. Nothing reads `console` or `pageerror` events, so a JS job keeps no record of what its pages printed.
- **Verbose is a log level only.** The per-page detail the spider has is logged at DEBUG: captured API URLs, the scroll count, and response-handler errors (`js_spider.py`, lines 263, 273, 275). Those lines are hidden at the default `LOG_LEVEL` of INFO ([`settings.py`](../../Scraping_project/src/settings.py), line 203).
- **A slow-response warning already exists.** `PrometheusExtension.response_received` ([`scrapy_prometheus.py`](../../Scraping_project/src/scrapy_prometheus.py), lines 423-428) logs `Slow response in <spider>` at WARNING when `download_latency` is over 5.0 s. The extension sits in the project `EXTENSIONS` default ([`settings.py`](../../Scraping_project/src/settings.py), line 282), so it applies to every spider unless config overrides that key. A JS render gets this warning today, but the warning names no page list and nothing keeps a sample.
- **The dashboard is read-only.** [`serve.py`](../../Scraping_project/dashboard/serve.py) routes `/api/metrics`, `/api/health`, and `/api/queues` (`API_ROUTES`, line 423). It has no POST handler.
- **Redis evicts only keys with a TTL.** Redis runs `volatile-lru` ([`REDIS_CAPACITY.md`](../../Scraping_project/docs/REDIS_CAPACITY.md), line 15). A key without a TTL is never evicted, and at `maxmemory` writes fail (line 57).
- **The spider is gated.** `javascript` refuses to crawl unless `ENABLE_EXPERIMENTAL_SPIDERS=1` ([`gate.py`](../../Scraping_project/src/stage1/experimental/gate.py)). The pane has nothing to show until someone opts in.
- **The product rule.** [ROADMAP](../ROADMAP.md) requires an on-call engineer to answer "last error?" from Control Center alone, "without DevTools or guessing ports" (line 85). The pane replaces opening DevTools. It is not a second tool.

## Library facts this design uses

Checked against the pins in [`requirements.txt`](../../Scraping_project/requirements.txt) (`scrapy-playwright==0.0.44`, `scrapy==2.13.3`) and against the Playwright 1.62.0 Python stubs on the machine used for this review. The repo pins Playwright 1.55.0, so confirm the Playwright names on that pin when the code lands.

- **Listeners can run before navigation.** scrapy-playwright attaches `playwright_page_event_handlers` from the request meta with `page.on(...)` before `page.goto`. It also runs `playwright_page_init_callback`, a dotted path called as `(page, request)` before `goto`. By the time `parse()` runs, the page has navigated and finished the `networkidle` page method, so a listener added in `parse()` misses everything printed during load.
- **Meta must stay picklable.** The spider settings builder `get_spider_settings` ([`spider_config.py`](../../Scraping_project/src/stage1/middlewares/spider_config.py), line 98) sets `SCHEDULER_DISK_QUEUE` to `PickleFifoDiskQueue`, and the JS spider spreads that builder into its `custom_settings`. Scrapy writes requests to a disk queue only when `JOBDIR` is set, and then it pickles them. A bound method or closure in `meta` can break that. A dotted-path string does not.
- **`download_latency` covers the render.** scrapy-playwright sets `request.meta["download_latency"]` after `goto`, the page methods (the `networkidle` wait), and the content read. It excludes scheduler queueing.
- **Console and error objects.** `ConsoleMessage` has the properties `type`, `text`, `location`, and `page`. A `pageerror` handler receives an `Error` with `message`, `name`, and `stack`. It carries no page reference.
- **CDP is Chromium-only.** The Playwright API marks `BrowserContext.new_cdp_session` as Chromium-only. `js_spider.py` sets `PLAYWRIGHT_BROWSER_TYPE` to `chromium` in `custom_settings` (line 38). The three criteria do not need CDP.

## 1. Show console errors sample

Acceptance criterion 1 says: "Show console errors sample."

**Capture before navigation.** Attach the listeners with `playwright_page_init_callback`, set to a dotted path to a module-level function in the spider package (name to be chosen). The function receives `(page, request)` and registers `console` and `pageerror` handlers. Each handler appends to a bounded list stored in `request.meta`. `parse()` reads that list from `response.meta`, and `handle_error()` reads it from `failure.request.meta`. The `handle_error()` path matters because a failed render often holds the most useful errors.

**What is kept.** Per page, at most 5 entries of type `error` or `pageerror`. With verbose on (criterion 2), the same cap also covers `log`, `info`, and `warning`. Each entry holds `ts`, `type`, `page_url` (the request URL), and `text` (cut to 300 characters). Nothing else is kept: no headers, cookies, request bodies, or form values. Console text is third-party content.

**Where it goes.** Flush once per page, in `parse()` and in `handle_error()`, to a capped Redis list `js_spider:console_samples`:

- `LPUSH` the new entries, then `LTRIM js_spider:console_samples 0 99`, then `EXPIRE js_spider:console_samples 86400`, in one pipeline per flush.
- The TTL is required. Under `volatile-lru`, a key with a TTL can be evicted under memory pressure, and a key without one cannot ([`REDIS_CAPACITY.md`](../../Scraping_project/docs/REDIS_CAPACITY.md), line 70). The sample is diagnostic, so eviction under pressure is acceptable.
- 86400 seconds matches the default `js_queue_ttl_seconds` in [`JS_RENDERING.md`](../../Scraping_project/docs/JS_RENDERING.md).
- A failed write logs a WARNING and increments `js/console/write_failed`. The render still completes.

**Counts.** Scrapy stats, in the `prefix/key` style of `memory_soft_stop/` ([`memory_soft_stop.py`](../../Scraping_project/src/memory_soft_stop.py), line 161):

- `js/console/error` and `js/console/pageerror` count every message seen, not only the kept ones.
- `js/console/dropped` counts entries past the per-page cap.
- `js/console/write_failed` counts failed flushes.

Stats hold counts only. Message text never goes into stats.

**Pane.** A new read-only route (for example `/api/js-debug`) in `serve.py` returns the last 100 entries with `LRANGE`. If Redis is down it returns 503 and the panel says "unavailable", never zeros, the same rule the `/api/queues` panel follows ([`dashboard/README.md`](../../Scraping_project/dashboard/README.md), the "Redis queue depths (#401)" paragraph). The route does not reuse `CC_QUEUE_KEYS`, which reports lengths, not contents. The pane's HTML, CSS, and JS live in `dashboard/*`, which is not part of this chain.

## 2. Toggle verbose

Acceptance criterion 2 says: "Toggle verbose."

- **Off by default.** The switch is a spider argument, `scrapy crawl javascript -a verbose=1`, read from `kwargs` the way `scout_spider.py` reads `job_id` (line 90). The value arrives as a string, so parse it with `parse_bool` from `src/utils/feature_flags.py`, as `gate.py` does. It is not a `config.yml` key. A config key would change the default for every run, and `config.yml` is outside this chain.
- **When on,** the sample also keeps `log`, `info`, and `warning` messages under the same caps, and the three DEBUG lines in `js_spider.py` (lines 263, 273, 275) log at INFO. With the flag off, output matches today.
- **A live toggle is a separate decision.** Flipping verbose during a crawl needs a write path. The dashboard has no POST handler today. A live switch means a new write route plus a Redis flag that the spider reads per page. This design recommends the start-time flag first.

## 3. Perf warning

Acceptance criterion 3 says: "Perf warning."

- **Measure with `download_latency`.** The JS spider already receives it on every response and it needs no extra browser call. It includes navigation and the `networkidle` wait, and it excludes scheduler queueing. The scroll loop in `parse()` (`_simulate_scrolling`, `js_spider.py` lines 245-263, up to five 500 ms waits) runs after the measurement and is not counted.
- **One threshold, one warning.** Reuse the 5.0 s value in `scrapy_prometheus.py` (line 427). Do not add a second WARNING line. The JS spider adds a `slow_render` entry (page URL and seconds) to the same sample list and increments `js/render/slow`.
- **The value is not measured.** 5.0 s is the number already in the code. A JS-specific value needs a baseline run, and this chain runs no live crawls.

## Criteria map

| Criterion | Designed in | Code it needs when built | Blocked for this chain |
|---|---|---|---|
| 1. Show console errors sample | Section 1 | `js_spider.py`: init callback, sink, flush. `docs/REDIS_CAPACITY.md`: new key | Pane: `dashboard/serve.py`, `app.js`, `index.html` |
| 2. Toggle verbose | Section 2 | `js_spider.py`: `verbose` kwarg | Live toggle: a dashboard write route |
| 3. Perf warning | Section 3 | `js_spider.py`: threshold check and stat | None. The threshold stays in `scrapy_prometheus.py` |

## Not in this design

- **No remote debugging port.** `--remote-debugging-port` or an exposed CDP socket gives full browser control over the network. Nothing here opens one.
- **CDP is optional later.** A CDP session (`new_cdp_session`, Chromium-only) could add browser-level `Log.entryAdded` events, such as failed network requests. The three criteria do not need it.
- **No change to the gate.** The pane shows nothing until a JS job runs with `ENABLE_EXPERIMENTAL_SPIDERS=1`.

## Owner decisions

1. **Verbose.** A start-time flag (recommended) or a live toggle with a write route.
2. **Sample storage.** A Redis list with a 24-hour TTL (proposed; about 100 entries of 300 characters, and Redis is already a requirement of this spider), or a Delta table (touches `lakehouse_manager.py`, which this chain does not touch).
3. **Slow threshold.** Keep 5.0 s (proposed; already in code), or set a JS-specific value after a measured baseline.

## Verification

- The sources named above were checked. The pins are `scrapy-playwright==0.0.44` and `scrapy==2.13.3`. Playwright was checked on 1.62.0, and the repo pins 1.55.0.
- No test or CI job reads `docs/product/`. CHANGELOG has no entries for the other spec docs (#749, #750, #751, #769, #780), so this file needs no CHANGELOG line.
- No code, dashboard, config, or lockfile changed.
