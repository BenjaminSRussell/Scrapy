# JS rendering: queue bounds and resource blocking

Settings for the Playwright-backed `javascript` spider (`src/stage1/experimental/js_spider.py`).
They all live in the `stage1:` block of `config.yml`.

## Priority queue bounds (#377)

`JSPriorityQueue` (Redis sorted set `js_spider:priority_queue`) is bounded:

| Key | Default | Effect |
|---|---|---|
| `js_queue_max_size` | `100000` | Maximum number of queued candidates. On overflow the **lowest-priority** members are evicted (`ZPOPMAX`), so a low-priority arrival into a full queue is rejected. `0` = unbounded. |
| `js_queue_ttl_seconds` | `86400` | Candidates queued longer than this are dropped on the next enqueue or dequeue (enqueue times live in `js_spider:priority_queue:enqueued_at`). `0` = never. |

The metadata hash and the enqueue-time index are trimmed together with the queue. An evicted
URL stays in the `:hashes` claim set, so the same crawl doesn't re-queue it. Clear the queue
(`JSPriorityQueue.clear()`) to start fresh.

Metrics: `js_priority_queue_size{queue}` and `js_priority_queue_evictions_total{queue,reason="overflow"|"ttl"}`.

## Resource blocking (#390)

| Key | Default |
|---|---|
| `js_blocked_resource_types` | `[image, stylesheet, font, media]` |
| `js_blocked_resource_types_by_domain` | `{}` |

Valid types are Playwright's `Request.resource_type` values: `stylesheet`, `image`, `media`,
`font`, `script`, `texttrack`, `xhr`, `fetch`, `eventsource`, `websocket`, `manifest`, `other`.
`document` is never blocked. An unknown type fails at startup.

```yaml
stage1:
  js_blocked_resource_types: [image, stylesheet, font, media]
  js_blocked_resource_types_by_domain:
    catalog.example.edu: [image, font]   # needs its CSS: content is revealed by stylesheet rules
    media.example.edu: []                # block nothing
```

A domain key matches the rendered page's host and its subdomains, and the most specific key
wins. The match is on the page being rendered, not on the CDN serving the subresource.

Blocking is applied from the first subresource through scrapy-playwright's
`PLAYWRIGHT_ABORT_REQUEST`, and again by the in-page route while the spider scrolls. Before
#390 the block was only installed after the page reached `networkidle`, so the initial load
fetched everything.

**Tradeoffs**

- Blocking `image`/`media`/`font` is almost always safe for link discovery and cuts bandwidth and render time sharply.
- Blocking `stylesheet` is cheap, but it can hide content on sites that reveal text or trigger lazy loading through CSS (e.g. `display:none` until a class is added, or infinite scroll that depends on layout height). Override those domains.
- Blocking `script`, `xhr` or `fetch` usually defeats the point of rendering. Only do it for a domain where you know the content is server-rendered.

## Presets and memory budget (#460)

The `javascript` spider keeps one Chromium page per in-flight render. Memory grows with the number of
live pages, so the budget is set by the page cap, not by the queue length.

| Key (`stage1:` in `config.yml`) | Default | Effect |
|---|---|---|
| `js_spider_preset` | `default` | `default`: 20 requests, 10 per domain, 4 contexts x 8 pages. `low_memory`: 4 requests, 2 per domain, 2 contexts x 2 pages. |
| `js_max_concurrent_pages` | `null` | Caps live Playwright pages. Split into contexts (up to the preset's count) and pages per context, so the product never exceeds the cap. `null` keeps the preset's own split. |
| `js_estimated_mb_per_page` | `100` | Per-page figure for the memory estimate. A configured guess, not a measurement. |
| `js_memory_warning_mb` | `4096` | At startup the spider logs a warning when pages x MB per page is above this. |

Environment overrides: `PLAYWRIGHT_MAX_CONCURRENT_PAGES` takes the place of `js_max_concurrent_pages`. Without it,
`PLAYWRIGHT_MAX_CONTEXTS` and `PLAYWRIGHT_MAX_PAGES_PER_CONTEXT` still override the preset's fields one at a time.

```yaml
stage1:
  js_spider_preset: low_memory
  js_max_concurrent_pages: 6   # 2 contexts x 3 pages
```

The Control Center System tab has a "JavaScript render budget" card. It computes the same split and estimate from
the values you type, and shows the `PLAYWRIGHT_MAX_CONCURRENT_PAGES` line to apply. It does not save anything or
change the running spider.

**Benchmarks status.** This repository has no measured JS spider benchmark yet. The "about 4 GB" and "up to 12 GB"
figures in the experimental gate (`src/stage1/experimental/gate.py`) are prerequisites, not measurements. Measure
RSS per page on your host, then set `js_estimated_mb_per_page` to that number.
