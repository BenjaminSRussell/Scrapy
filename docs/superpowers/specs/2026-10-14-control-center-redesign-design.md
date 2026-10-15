# Control Center redesign

Date: 2026-10-14. Branch: `feat/control-center-redesign` (from `origin/main` at 52e5ccc).

## Why

The Pipeline Control Center (`Scraping_project/dashboard/`) looks like a generic admin
template, and it does not show the pipeline. Every stage number it renders comes from
Prometheus series (`stage1_urls_discovered_total`, `stage2_pages_analyzed_total`, ...)
that only `temp_scripts/enhanced_metrics_exporter.py` emits. Against the real stack
the stage cards read zero. The pipeline's real state lives in Delta Lake tables and
Redis, which the page never reads.

## Goals

1. Show what the pipeline is really doing, live, from Delta Lake and Redis.
2. Show *how* data is collected: URLs moving between stages as it happens.
3. Layered navigation in the style of an iOS app: pipeline, then stage, then one URL.
4. Apple look and feel: system fonts, iOS system colors, translucent bars, large titles,
   inset grouped lists, light and dark.
5. First-class on iPhone: bottom tab bar, safe areas, 44 pt targets, Add to Home Screen,
   VoiceOver, Dynamic Type, reduced motion.
6. A demo mode that simulates a realistic crawl, for screenshots and for using the UI
   without the stack running.

## Non-goals

Crawl controls (start/stop), new auth, Grafana embedding, history beyond 30 minutes in
memory, a service worker or offline use, Kafka topic reading, push notifications.

## Decisions

- **Clean slate for the UI.** `app.js`, `format-utils.js`, `features.js`, `styleguide.html`,
  `overview_screenshot.py` and the 30 old `tests/*.test.js` files are deleted. The 28 open
  PRs and 68 open issues that target the old page are listed for the owner to triage;
  nothing is closed automatically.
- **Keep `serve.py`.** Its auth gate, rate limiter, CSP, static allowlist, loopback default
  and their 33 tests stay. New endpoints are added beside `/api/metrics`, `/api/health` and
  `/api/queues`, which keep working.
- **No Node toolchain.** The UI is native ES modules. Preact and htm are vendored under
  `dashboard/vendor/` (pinned versions, bare imports rewritten to relative paths, licenses
  kept). Chart.js and the jsDelivr CSP allowance are removed; charts are hand-drawn SVG.
  `dashboard/package.json` exists only to set `"type": "module"` for `node --test`; the
  server never serves it.
- **Server-sent events** for live updates. Fits the threaded stdlib server, works with
  Basic auth, and reconnects on its own.

## Data

### Stages

| id | Title | Table | Flows to |
|---|---|---|---|
| `seeds` | Seeds | `seed_urls` | discovery |
| `discovery` | Discovery | `stage1_discovery` | analysis |
| `analysis` | Analysis | `stage2_page_analysis` | summaries, large_docs |
| `summaries` | Summaries | `stage3_summaries` | (end) |
| `large_docs` | Large Docs | `stage4_large_doc_summaries` | (end) |

`stage1_errors`, when present, feeds error counts and error events for discovery.

The tables on disk do not match `src/core/schemas.py`. For example, `stage1_discovery`
has `domain`, `depth`, `status_code`, `content_size`, `requires_js`, `urls_found` and
`_ingestion_time`, but no `parent_url`. Timestamps are ISO strings. Every table has
`_ingestion_time`. So:

- Each stage declares the columns it wants. The reader asks for the columns that exist
  and treats the rest as absent. The UI hides a field it does not have.
- `_ingestion_time` is the event time for every table.
- "Found on" (`parent_url`) shows in a URL journey only when that column exists.

### Live reader (`ccfeed/delta.py`)

One watcher per table:

- Opens `DeltaTable` once and calls `update_incremental()` each poll. Nothing more is
  done when the version has not changed.
- Row count is the sum of `num_records` in the add actions. No data is read for counts.
- New rows are read only from data files that were not in the previous file set, with
  pyarrow and column projection. Rows at or before the table's watermark (the latest
  `_ingestion_time` seen) are dropped, so files rewritten by MERGE do not replay old rows.
  At most 500 new rows per table per poll are kept (the newest).
- First poll: the existing files become the known set. The 50 newest rows from the
  most recently modified files seed the "recent" lists, but are not emitted as live events.
- A missing table reports `missing`. A read error reports `error` with the exception
  type. Neither ever reports 0 as if it were a count.

### On-demand queries (`ccfeed/queries.py`)

Cached per table version. Recomputed only when the version changes.

- **Stage detail:** totals; breakdowns per stage (discovery: depth histogram, status
  codes, share needing JS; analysis: routed to summaries vs large docs, mean word count;
  summaries: model counts; large docs: mean compression); the 50 newest rows.
- **URL journey:** every row for one exact URL across the stage tables, with the fields
  each stage has.
- **Explore:** a tree built from the discovery table's `url` column: host, then path
  segments. A request for a path returns its children with URL counts, plus a depth
  histogram at the root. A node with more than 200 children returns the top 200 by count
  and a remainder count.

### Services

Checked every 10 s, not every tick:

- **Redis:** ping latency, plus depth of `CC_QUEUE_KEYS` (existing helper).
- **Delta Lake:** the base path exists, how many stage tables were found, the newest commit.
- **Metrics upstream:** the existing `fetch_upstream` reachability check.

Each has a status of `ok`, `warn`, `down` or `unknown`, plus a one-line detail.

Stage freshness comes from the newest `_ingestion_time`: active (under 2 min), idle
(under 30 min), stale (30 min or more), or no data. Workers write no heartbeat, so the
UI says "last write 3 min ago". It never says "Running".

### Demo feed (`ccfeed/demo.py`)

A seeded `random.Random` simulates a crawl of UConn-style hosts (`uconn.edu`,
`admissions.uconn.edu`, `library.uconn.edu`, `today.uconn.edu`, ...) with a plausible
path vocabulary:

- Discovery picks a known page as the parent, so `depth` = parent depth + 1.
- Analysis routes about 5% of pages to Large Docs and about 70% to Summaries.
- About 2% of fetches become errors (timeouts, 404s).

Rates: roughly 40 to 120 discoveries per minute, 30 analyses per minute, 20 summaries
per minute and 1 large doc per minute. Before the first request it pre-runs 30 simulated
minutes, so charts start full. It implements the same interface as the live feed,
including detail, journey and explore, from memory. `--demo-seed` makes runs repeatable.
The UI labels demo mode everywhere it shows data.

### Hub (`ccfeed/hub.py`)

- A background thread polls the feed every `CC_FEED_INTERVAL` seconds (2 s live,
  1 s demo).
- It keeps 30 minutes of per-stage counts in 10 s buckets. Rates per minute come from
  real timestamps.
- It keeps the last 500 events, and builds a snapshot dict.
- It publishes to subscribers. Each subscriber has a bounded queue (64). A full queue
  drops events and marks the subscriber for a fresh snapshot.
- A failing poll is logged, retried with backoff and shown in the snapshot. It never
  kills the thread.

The hub starts lazily on the first data request, so importing `serve.py` and the
existing tests spin no threads.

### HTTP API (added to `serve.py`)

All endpoints sit behind the existing guard (rate limit, then auth).

| Route | Returns |
|---|---|
| `GET /api/snapshot` | `{mode, generated_at, interval, stages[], edges[], services[], series{}, recent[]}` |
| `GET /api/stream` | SSE: `snapshot` on connect and when resync is needed, then `tick` (stage and service changes plus new events) each poll, plus a `: ping` comment every 15 s. Concurrent streams are capped by `CC_MAX_STREAMS` (default 16); over the cap the server returns 503 |
| `GET /api/stage/<id>` | Stage detail; 404 for an unknown id |
| `GET /api/url?u=<url>` | URL journey; 400 for a non-http(s) URL, 404 when the URL is not found |
| `GET /api/explore?path=<host/seg/...>` | Explore node; 404 for an unknown path |

`serve.py` gains `--demo`, `--demo-seed`, `--delta-path` (default `DELTA_LAKE_PATH`, then
`Scraping_project/data/delta_lake`) and `--interval`, each with a `CC_*` env equivalent.
`package.json`, `tools/` and `ccfeed/` are never served.

## UI

### Structure

Tabs: a bottom tab bar on iPhone, a sidebar at 900 px and wider.

- **Pipeline:** home; pushes stage, then URL.
- **Explore:** host, then path, ..., then a URL list; pushes URL.
- **Activity:** the live event log; pushes URL.
- **Health:** services, stage freshness, links.

The route is the hash: `#/<tab>/<kind>:<value>/...`. `kind` is `stage`, `path` or `url`,
and `value` is URI-encoded. Every layer is a real history entry, so iOS edge-swipe back
and the browser back button both work.

Each layer has a large title that collapses into the translucent nav bar on scroll, and
a back button labelled with the parent layer's title. Push and pop use the View
Transitions API (slide in from the right, parallax under). With reduced motion, or
without the API, the switch is instant.

On a route change, focus moves to the new layer's title, and `document.title` becomes
`<Layer> - Control Center`.

### Pipeline (home)

- **Status pill** in the nav bar: Live (green, pulsing), Demo (purple), Reconnecting
  (orange) or Offline (red). Each always has its text label as well as its color.
- **Flow diagram.**
  - Layout: one column on phones (Seeds, Discovery, Analysis, then Summaries and Large
    Docs side by side); horizontal when the container is wide (CSS container query).
  - Each node is a link to its stage with: a tinted icon tile, title, count in SF
    Rounded, rate per minute (or "Idle"), freshness, and a "can't read" or "no data" state.
  - An SVG overlay draws the edges from measured node positions (`ResizeObserver`).
  - Each new event animates a dot along its edge. Seeds to discovery comes from a
    discovery event; discovery to analysis from an analysis event; and so on.
  - Budget: 6 dots per edge per second and 60 in total. When that budget is spent, the
    node count just ticks up.
  - No dots under reduced motion or when the tab is hidden.
  - The SVG is `aria-hidden`; each node has a full `aria-label`.
- **Throughput:** an area chart for the last 30 minutes, with a segmented control for
  Discovery, Analysis or Summaries. Scrub by touch, mouse or the arrow keys to read any
  point. A text summary (now, peak, average) is available to screen readers.
- **Live:** the 8 newest events as rows (stage glyph, host in bold, path, relative time).
  Tapping a row pushes that URL.
- **Health summary row:** "All systems normal" or "2 issues". Tapping it opens the
  Health tab.

### Stage

- Tiles: Total, Per minute, Last write. Below them, the stage's chart.
- The breakdown cards from the stage detail. Bars are drawn as horizontal SVG, with text
  values next to them.
- **Recent:** the API's newest rows, with live events for this stage merged at the top as
  they arrive. Tapping a row pushes the URL.
- The detail refetches when the stage's table version changes, at most once every 5 s.

### URL journey

- The title is the host, with the path as the subtitle.
- An Open Page link (http(s) only, `rel="noopener noreferrer"`).
- A Share button: `navigator.share` when available, else copy the link.
- A vertical timeline with Seeded, Discovered, Analyzed, then Summarized or Large Doc.
  Each step shows its fields and time, or "Waiting" / "Not routed here".
- A "Found on" link appears when `parent_url` exists.

### Explore

- A search field filters the current level.
- A squarified treemap of the current level's children. Tiles are colored by host;
  tapping one drills down.
- Below it, the same children as list rows with counts. The list is the accessible
  primary path.
- A depth histogram at the root.
- At a leaf, a list of URLs, each of which pushes the URL journey.

### Activity

- A segmented filter: All, Found, Analyzed, Summarized, Errors.
- A search field (substring match).
- Pause and Resume. While paused, a "N new" pill appears; tapping it resumes and scrolls
  to the top.
- Holds up to 500 events.
- A polite live region announces at most one summary every 10 s ("12 new events").

### Health

Inset grouped sections:

- **This dashboard:** stream state, mode, version.
- **Services:** Redis (latency and queue depths), Delta Lake (path, tables found, last
  commit), metrics upstream.
- **Stages:** last write and freshness.
- **Links:** Grafana (`CC_GRAFANA_URL`).

### Visual system (`styles.css`)

- **Fonts:** `-apple-system, BlinkMacSystemFont, system-ui, ...` for text, and
  `ui-rounded` for big numbers, with `tabular-nums`. On iOS, `html { font: -apple-system-body }`
  makes every rem-based size follow Dynamic Type. The type ramp follows iOS: large title
  34, title2 22, headline 17 semibold, body 17, subhead 15, footnote 13, caption 12.
- **Colors:** iOS system colors with light and dark values: blue, green, indigo, orange,
  pink, purple, red and teal; grouped backgrounds `#F2F2F7`/`#000`; cells `#FFF`/`#1C1C1E`.
  Secondary label is darkened to 4.5:1 in light mode. Colored text uses darker
  "text-safe" variants; bright system colors are used only for fills, dots and large
  glyphs.
- **Materials:** nav and tab bars use `backdrop-filter: saturate(180%) blur(20px)` over a
  semi-opaque fill, with hairline separators.
- **Layout and touch:**
  - Inset grouped lists with 10 pt corner radius; cards with 16 to 20 pt radius.
  - Safe-area insets on all bars.
  - A 44 pt minimum touch target, `touch-action: manipulation`, and iOS-style pressed
    states.
- **Theme:** follows `prefers-color-scheme`. `prefers-contrast: more` strengthens
  separators and labels. `forced-colors` falls back to system colors.
- **Home screen:** `manifest.json` (`display: standalone`), a 180 px apple-touch-icon, a
  512 px icon, an SVG favicon, `viewport-fit=cover` and
  `apple-mobile-web-app-status-bar-style: black-translucent`.

### Client modules (`dashboard/app/`)

| Module | Purpose |
|---|---|
| `main.js` | Bootstrap and app shell (tabs, sidebar, layer outlet, view transitions) |
| `router.js` | Pure: `parse(hash)` to a route, `format(route)` to a hash, parent and title helpers |
| `store.js` | Pure reducers (apply snapshot, apply tick, cap events, filters) plus the EventSource connection with state tracking |
| `api.js` | JSON fetches with `AbortController` and typed errors |
| `format.js` | Pure: compact numbers, rates, relative time, URL split, bytes, freshness labels |
| `ui/*.js` | Nav bar, list rows, status pill, segmented control, search field, skeleton, icons, area chart, bar list, flow diagram, treemap (squarify is pure) |
| `views/*.js` | pipeline, stage, url, explore, activity, health |

All text is rendered through Preact, which escapes it. No `innerHTML`.

## Errors and edge cases

- **Stream drops:** the pill shows Reconnecting, numbers dim, and "Updated 45 s ago"
  appears. After 3 failed attempts a banner explains it. EventSource retries on its own.
- **Failed layer fetch:** an inline card with Retry. A 404 shows "Not found" with a
  route back.
- **Unreadable or missing table:** that node, stage and Health row say so. Other stages
  are unaffected.
- **Long URLs:** ellipsis with the full text in `title` and the accessible name.
  Non-http(s) URLs are never linked.
- **Event floods:** the server caps rows per poll; the client caps the list and dots.

## Testing

- **Python** (`tests/unit/dashboard/`, run by the main CI):
  - Live watcher against temporary Delta tables written with `write_deltalake`: counts,
    new-row detection, watermark dedupe, missing columns, missing tables.
  - Queries: stage detail, journey and explore.
  - Demo: determinism with a seed, event shape, rates in range.
  - Hub: rates from a fake clock, slow subscriber gets a resync.
  - Endpoints on a real server on an ephemeral port: snapshot, stage, url, explore,
    stream first frames, stream cap returning 503, and auth applied to the new routes.
  - The existing 33 serve tests stay green. Two that name old assets are updated to
    the new ones.
- **JS** (`node --test`, run by `ci-dashboard.yml`): router round-trips, store reducers,
  format helpers, squarify layout, particle budget, chart scales.
- **Browser smoke** (`dashboard/tools/smoke.py`, Playwright on demo mode, iPhone 15
  viewport and desktop, light and dark):
  - Loads with no console errors and a live pill.
  - Pipeline, then Discovery, then a URL, then back.
  - Explore drill-down, Activity filter, Health.
  - Writes screenshots. The visual workflow runs it and uploads them.

## Rollout

1. Backend feed, then API, with tests.
2. Vendored libraries, shell, router and store.
3. Views, flow diagram and charts.
4. Compose: mount Delta read-only and set `DELTA_LAKE_PATH`, `REDIS_HOST` and
   `METRICS_UPSTREAM` for the dashboard service.
5. README, `A11Y_CHECKLIST.md`, CI updates.
6. Triage list of the open PRs and issues for the owner.
