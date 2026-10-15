# Pipeline Control Center

A live, iPhone-first view of the scraping pipeline. It shows what each stage is
doing right now and how URLs move through it: every dot on the Pipeline screen is
a URL travelling to its next stage. It reads the pipeline's own Delta Lake tables
and Redis, and it never shows a number it was not given.

```bash
python Scraping_project/dashboard/serve.py            # live: reads Scraping_project/data/delta_lake
python Scraping_project/dashboard/serve.py --demo     # a simulated crawl, no services needed
# open http://localhost:8080
```

Grafana stays the place for history, alerts and custom queries. Control Center
links to it from Health (`CC_GRAFANA_URL`, default `http://localhost:3000/`) and
does not embed it.

## What you see

Four tabs: a bottom tab bar on iPhone, a sidebar at 900 px and wider. Every tab is a
stack of layers, like an iOS app. Each layer is a real history entry, so the back
button and edge-swipe back both work.

| Tab | Root layer | Pushes |
|---|---|---|
| **Pipeline** | The flow diagram: Seeds → Discovery → Analysis → Summaries / Large Docs, with live counts, rates and freshness. Also a throughput chart (scrub it), the newest events and a health summary | Stage → URL |
| **Explore** | Everything discovered, host then path, as a treemap and a list, plus a crawl-depth histogram | Path → … → URL |
| **Activity** | Every row the pipeline writes, newest first. Filter by kind, search URLs, pause and resume | URL |
| **Health** | The stream, Redis (latency, queue depths), Delta Lake, the metrics upstream, and each stage's newest write | Stage |

A **stage** layer shows totals, its rate over 30 minutes, breakdowns, and its newest
rows, with live rows landing on top:
- Discovery: depth, status codes, share needing JavaScript, mean page size.
- Analysis: routing split and mean word count.
- Summaries: models used.
- Large Docs: mean compression.

A **URL** layer is that URL's journey:
- What every stage recorded about it, or why it skipped one ("Not a seed", "Routed to
  Large Docs", "Low-quality page").
- Open Page and Share.
- "Found on", when the table records a parent.

The status pill always says where the numbers come from: **Live**, **Demo**,
**Reconnecting** or **Offline**.

## Where the numbers come from

| Shown | Source |
|---|---|
| Stage counts | Sum of `num_records` in each table's Delta add actions (no data is read) |
| Newest write and freshness | `max._ingestion_time` file stats. Active under 2 min, idle under 30 min, stale after that. Workers send no heartbeat, so this is the honest signal |
| Live events | Rows in data files that are new since the last poll, read with column projection. Rows at or before the table's watermark are dropped, so compaction and MERGE rewrites never replay history |
| Rates | Counts sampled every poll, in 10 s buckets from real timestamps. The headline rate is the last 60 s; the chart plots 30 s means |
| Charts at startup | Backfilled from `_ingestion_time`, so the 30-minute chart is full on page load |
| Errors | `stage1_errors` row count and new rows |
| Redis | `PING` latency, plus the type-aware length of each key in `CC_QUEUE_KEYS` |

Stage tables: `seed_urls`, `stage1_discovery`, `stage2_page_analysis`,
`stage3_summaries` and `stage4_large_doc_summaries` under `DELTA_LAKE_PATH`. The tables
on disk do not match `src/core/schemas.py` exactly, so each stage asks for the columns
it wants and hides the fields a table doesn't have:
- A missing table reads "No data yet".
- An unreadable table reads "Can't read table", with the error type.
- Neither is ever shown as 0.

`--demo` runs a seeded simulation of a crawl over UConn-style hosts:
- It has the same shape and API as the live feed.
- It warms up 30 simulated minutes before the first request, so charts start full.
- Every screen labels it as demo data.

## Running it on an iPhone

The server binds to loopback by default. To reach it from a phone on your LAN, bind
to all interfaces and turn auth on:

```bash
CC_HOST=0.0.0.0 CC_BASIC_AUTH='ops:a-long-random-password' python Scraping_project/dashboard/serve.py
```

Open `http://<your-mac>.local:8080` in Safari, sign in, then **Share → Add to Home
Screen**. It opens full screen with its own icon.

Phone layout:
- Text follows the iOS Text Size setting (Dynamic Type).
- Light and dark mode follow the system.
- Reduce Motion stops every animation, including the dots.

Over plain HTTP, Share falls back to copying the link.

## Server

`serve.py` is the standard-library threaded server. It serves only the dashboard
assets and the API below, and puts every request through a rate limit and then the
optional auth gate.

| Route | Returns |
|---|---|
| `GET /api/snapshot` | `{mode, generated_at, interval, poll_error, stages[], edges[], services[], series{}, recent[]}` |
| `GET /api/stream` | Server-sent events: `snapshot` on connect (and after a slow client falls behind), then `tick` each poll, and `: ping` every 15 s. More than `CC_MAX_STREAMS` open streams get 503 |
| `GET /api/stage/<id>` | `{stage, breakdowns[], recent[]}`; 404 for an unknown stage |
| `GET /api/url?u=<url>` | `{url, host, path, parent_url, steps[]}`; 400 unless `u` is http(s) and at most 4096 characters; 404 if no stage has it |
| `GET /api/explore?path=<host/seg/…>` | `{path, total, children[], other, urls[], depths}`; 404 for an unknown path |
| `GET /api/queues`, `/api/health`, `/api/metrics` | Kept from the previous dashboard for scripts: Redis depths, upstream reachability, and the metrics proxy |

The feed hub starts on the first data request (or at launch from `main`). It polls
every `CC_FEED_INTERVAL` seconds and keeps 30 minutes of history and the last 500
events in memory. A failed poll is reported in the snapshot as `poll_error` and is
retried with backoff; it never stops the stream.

| Setting | Default | Purpose |
|---|---|---|
| `--demo` / `CC_DEMO` | off | Simulated crawl instead of Delta Lake |
| `--demo-seed` / `CC_DEMO_SEED` | `7` | Makes the demo repeatable |
| `--delta-path` / `DELTA_LAKE_PATH` | `Scraping_project/data/delta_lake` | Lake to read (read-only) |
| `--interval` / `CC_FEED_INTERVAL` | `2` (demo `1`) | Seconds between polls |
| `CC_MAX_STREAMS` | `16` | Concurrent `/api/stream` clients |
| `--host`/`--port`, `CC_HOST`/`CC_PORT` | `127.0.0.1` / `8080` | Bind address. A wildcard bind prints a warning, and a second one if auth is off |
| `CC_AUTH_TOKEN` | unset | Shared secret: `Authorization: Bearer <token>`, or the password at the browser prompt |
| `CC_BASIC_AUTH` | unset | `user:password` for the browser login prompt |
| `CC_RATE_LIMIT` / `CC_RATE_BURST` | `600` / `120` | Per-client requests per minute and burst; `0` disables. Excess gets 429 with `Retry-After` |
| `CC_CORS_ORIGINS` | unset | Exact origins allowed to read the API cross-origin; `*` is for local development only |
| `CC_CONNECT_SRC` | unset | Extra http(s) origins for CSP `connect-src` |
| `REDIS_URL`, or `REDIS_HOST`/`PORT`/`DB`/`PASSWORD` | `localhost:6379/0` | Redis for Health |
| `CC_QUEUE_KEYS` | `js_spider:priority_queue` | Redis keys whose depth Health shows |
| `METRICS_UPSTREAM` | `http://127.0.0.1:9090/metrics` | Checked for Health; proxied by `/api/metrics` |
| `CC_GRAFANA_URL` | `http://localhost:3000/` | Grafana home linked from Health (a `/d/<uid>` path is trimmed) |
| `CC_VERSION` | `git describe` | Version shown in the sidebar and on Health |

In Docker Compose (`--profile monitoring`), the `dashboard` service mounts `./data`
read-only and reads the same lake the workers write. It talks to `redis` and
`prometheus` on the compose network, and is published on loopback `:8080`.

### Security

- **Content Security Policy.** It is enforced, with no exceptions for inline code:
  - `default-src 'self'`; `script-src 'self'` and `style-src 'self'`, with no
    `'unsafe-inline'` and no third-party origins. Preact and htm are vendored under
    `vendor/`, and styles are set through the CSSOM.
  - `connect-src 'self'`, `object-src 'none'`, `base-uri 'none'`,
    `frame-ancestors 'none'`.
  - Plus `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
    `Permissions-Policy` and COOP.
- **Rendering.** All text is rendered through Preact, which escapes it, and the code
  never writes `innerHTML`. Only `http(s)` URLs become links, always with
  `rel="noopener noreferrer"`.
- **What is served.** Only asset types are served
  (`.html/.js/.css/.json/.png/.svg/.ico/.txt/fonts`). Directory listings, dotfiles,
  `tests/`, `tools/`, `ccfeed/`, `package.json` and source files return a plain 404.
- **Auth.** The gate is a shared secret for LAN use, not an identity provider. Beyond a
  trusted LAN, keep the server on loopback and put it behind a reverse proxy that
  terminates TLS and authenticates users.

## Code

```
serve.py            HTTP server, security layer, API routes, SSE
ccfeed/             data feed (Python)
  stages.py         stage table, timestamp parsing, freshness, events
  delta.py          TableWatcher: incremental, stats-first reads of one Delta table
  live.py           LiveFeed: watchers + queries + backfill + Delta service row
  demo.py           DemoFeed: seeded simulation with the same interface
  queries.py        stage detail, URL journey and explore builders (pure)
  history.py, hub.py rates, recent events, snapshots/ticks, subscribers
index.html, styles.css, manifest.json, icons/
app/                client (native ES modules, no build step)
  main.js           bootstrap, hash routing, view transitions, focus
  app.js            tab bar / sidebar and the current layer
  router.js, state.js, format.js, describe.js, meta.js   pure, tested with node
  layout/           squarify, scales and paths, edge geometry, particle budget (pure)
  store.js, api.js  EventSource store with connection states; JSON fetches
  ui/, views/       components and the six layer views
vendor/             preact 10.29.8, preact/hooks, htm 3.1.1 (licenses in LICENSES.txt)
tools/smoke.py      browser smoke test
```

`package.json` only marks the folder as ES modules for `node --test`. There are no
dependencies and nothing to install.

## Tests

```bash
node --test Scraping_project/dashboard/tests/                         # client: router, state, format, layouts
cd Scraping_project && pytest tests/unit/dashboard tests/unit/test_dashboard_serve_*.py --no-cov
python Scraping_project/dashboard/tools/smoke.py                        # browser: 4 profiles, screenshots in dashboard/visual/
```

CI runs them in three places:
- `ci-dashboard.yml`: the node tests and a syntax check of every module.
- The main workflow: the Python tests.
- `ci-dashboard-visual.yml`: the smoke test on pull requests that touch this folder.
  It uploads the screenshots as `dashboard-screenshots`.

Accessibility expectations for changes are in [A11Y_CHECKLIST.md](A11Y_CHECKLIST.md).
