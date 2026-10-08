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
