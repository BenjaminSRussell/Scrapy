# Control Center Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Control Center UI with a layered, iPhone-first, Apple-style dashboard
fed live from Delta Lake and Redis over SSE, plus a seeded demo feed.

**Architecture:** A `ccfeed` Python package (watchers, queries, demo, hub) sits beside
`serve.py`. `serve.py` keeps its security layer and gains JSON and SSE routes that read from
one lazily started `Hub`. The browser app is native ES modules with vendored Preact and htm.
Pure logic (router, reducers, formatting, layouts) lives in DOM-free modules tested with
`node --test`.

**Tech Stack:** Python 3.11+ stdlib `http.server`, `deltalake`, `pyarrow`, `redis`; Preact 10
with hooks and htm (vendored); SVG; Playwright (Python) for the smoke test.

**Spec:** `docs/superpowers/specs/2026-10-14-control-center-redesign-design.md`

**Execution note:** The plan's author runs it in the same session (native execution), so
tasks pin contracts and test cases. Code is written test-first inside each task.

## Global Constraints

- No Node toolchain. `package.json` holds only `{"private": true, "type": "module"}` and is never served.
- No third-party script origins. CSP `script-src 'self'`.
- No `innerHTML`. All text goes through Preact. Only `http:` and `https:` URLs become links.
- Never show a number the server did not report. Missing is "No data"; unreadable is "Can't read".
- Touch targets are at least 44×44 CSS px. Text contrast is at least 4.5:1 (3:1 for large text and glyphs).
- `prefers-reduced-motion: reduce` turns off particles, view transitions and pulses.
- Existing `serve.py` behaviour stays: loopback default, auth gate, rate limit, static allowlist, `/api/metrics`, `/api/health`, `/api/queues`, `/version.js`.
- Freshness thresholds: active under 120 s, idle under 1800 s, stale 1800 s or more, none when there has never been a write.
- History: 30 minutes in 10 s buckets (180 points). Events kept: 500 on the server, 500 on the client; 50 in a snapshot's `recent`.
- At most 500 new rows per table per poll. Per-subscriber queue: 64. `CC_MAX_STREAMS` defaults to 16.

## Review Focus

1. **Compaction or VACUUM removes a data file between listing and reading.** The watcher
   skips that file and the stage stays `ok`. Test in Task 2.
2. **Mixed timestamp formats** (`2026-09-21T13:47:50.281369` vs `...+00:00` vs `...Z`).
   Naive values are treated as UTC and all three parse. Test in Task 2.
3. **Hostile or odd URLs** (`javascript:`, 4 kB long, unicode). `/api/url` returns 400 for
   non-http(s); the client never links them, and long text ellipsizes. Tests in Tasks 5 and 6.
4. **A client disconnects mid-stream.** The handler thread exits and the hub's subscriber
   count goes back down, with no leak. Test in Task 5.
5. **Auth applies to the new routes,** SSE included: 401 without credentials when auth is
   on. Test in Task 5.

---

## File map

```
Scraping_project/dashboard/
  serve.py                    MODIFY  routes, SSE, CLI flags, CSP, denied names
  ccfeed/__init__.py          CREATE  exports
  ccfeed/stages.py            CREATE  STAGES, EDGES, freshness(), parse_ts()
  ccfeed/history.py           CREATE  History (buckets, rates)
  ccfeed/hub.py               CREATE  Hub, Subscription
  ccfeed/delta.py             CREATE  TableWatcher (live per-table polling)
  ccfeed/queries.py           CREATE  stage_detail / url_journey / explore builders (pure on row dicts + live loaders)
  ccfeed/live.py              CREATE  LiveFeed (watchers + queries + backfill + delta service row)
  ccfeed/demo.py              CREATE  DemoFeed
  package.json                CREATE
  index.html                  REWRITE
  styles.css                  CREATE
  manifest.json, icons/*      CREATE
  vendor/                     CREATE  preact.module.js, hooks.module.js, htm.module.js, LICENSES.txt
  app/lib.js                  CREATE  h, html, render, hooks re-export
  app/router.js, format.js, state.js            CREATE  pure
  app/layout/squarify.js, budget.js, scale.js  CREATE  pure
  app/store.js, api.js, main.js                CREATE
  app/ui/*.js, app/views/*.js                  CREATE
  tests/*.test.js             REPLACE (old 30 deleted)
  tools/smoke.py              CREATE  (replaces overview_screenshot.py)
  README.md, A11Y_CHECKLIST.md                 REWRITE
  DELETE: app.js, features.js, format-utils.js, styleguide.html, overview_screenshot.py, version.js (served dynamically)
Scraping_project/tests/unit/dashboard/test_*.py   CREATE
Scraping_project/tests/unit/test_dashboard_serve_security.py  MODIFY 2 tests (new asset names)
Scraping_project/docker-compose.yml                MODIFY dashboard service
.github/workflows/ci-dashboard.yml, ci-dashboard-visual.yml   MODIFY
```

## Contracts (shared by every task)

**Stage dict** (snapshot `stages[]`, tick `stages[]`, `stage` inside a stage detail):
```json
{"id": "discovery", "title": "Discovery", "table": "stage1_discovery",
 "status": "ok|missing|error", "error": null, "count": 5700, "rate_per_min": 84.0,
 "last_write": 1760400000.0, "freshness": "active|idle|stale|none", "version": 22110,
 "errors": 3}
```
`count`, `rate_per_min`, `last_write`, `version` and `errors` may be `null`. `errors` is
used only by discovery.

**Event:** `{"id": "discovery:812", "stage": "discovery", "kind": "seeded|discovered|analyzed|summarized|large_doc|error", "url": "...", "t": 1760400000.0, "fields": {...}}`.
`fields` is small: discovery `{depth, status_code}`; analysis `{word_count, routed: "summaries"|"large_docs"|null}`; error `{error_type}`.

**Service:** `{"id": "redis|delta|metrics", "label": "Redis", "status": "ok|warn|down|unknown", "detail": "2 ms · 1 queue", "checked_at": 1760400000.0, "data": {}}`.

**Snapshot:** `{"mode", "generated_at", "interval", "stages": [...], "edges": [{"from": "seeds", "to": "discovery"}, ...], "services": [...], "series": {"<stage>": [[t, rate_per_min], ...]}, "recent": [events newest first, ≤50]}`.

**Tick** (SSE `event: tick`): `{"generated_at", "stages", "services", "series_point": {"<stage>": [t, rate]}, "events": [oldest→newest]}`.
The client replaces the last series point when `t` matches it, and appends otherwise.

**Stage detail:** `{"stage": <stage dict>, "breakdowns": [{"id", "title", "kind": "bars", "items": [{"label", "value"}]} | {"id", "title", "kind": "stats", "items": [{"label", "value", "format": "int|float|ratio|bytes|percent"}]}], "recent": [{"url", "t", "fields"}]}`.

**URL journey:** `{"url", "host", "path", "parent_url": null|str, "steps": [{"stage", "title", "status": "done|waiting|skipped", "note": str|null, "t": float|null, "fields": [{"label", "value", "format": "int|bytes|bool|text|ratio|time"}]}]}`.
Steps are seeds, discovery, analysis, summaries, large_docs. Skip rules: not in seeds gives
"skipped" with the note "Not a seed". When analysis is done and `is_massive_doc` is true,
summaries is "skipped" with the note "Routed to Large Docs"; when it is false, large_docs is
"skipped" with "Routed to Summaries". When analysis is not done, both are "waiting".

**Explore node:** `{"path": ["uconn.edu", "admissions"], "total": 120, "children": [{"segment", "count", "leaf"}], "other": 0, "urls": [{"url", "depth"}], "depths": [{"label", "value"}] | null}`.
`children` holds at most 200, sorted by count descending. `urls` holds up to 100 URLs
ending exactly at this node. `depths` is present only at the root.

**Feed protocol (Python):**
```python
class Feed(Protocol):
    mode: str                                   # "live" | "demo"
    def poll(self, now: float) -> PollResult: ...
    def backfill(self, now: float, seconds: int, bucket: int) -> dict[str, list[tuple[float, int]]]: ...  # cumulative count at each bucket end
    def services(self, now: float) -> list[dict]: ...
    def stage_detail(self, stage_id: str) -> dict | None: ...
    def url_journey(self, url: str) -> dict | None: ...
    def explore(self, path: list[str]) -> dict | None: ...

@dataclass
class StageState:
    status: str = "missing"; count: int | None = None; last_write: float | None = None
    version: int | None = None; error: str | None = None; errors: int | None = None

@dataclass
class PollResult:
    stages: dict[str, StageState]
    events: list[dict]
```

**Hub:** `Hub(feed, interval=2.0, clock=time.time, probe=None)` where `probe() -> list[dict]`
supplies extra service rows (Redis and metrics in live mode). Methods: `start()`, `stop()`,
`step(now=None)`, `snapshot() -> dict`, `subscribe() -> Subscription`,
`unsubscribe(sub)`, and the property `subscriber_count`.
`Subscription.get(timeout) -> tuple[str, dict] | None`.

**Client route:** `{tab: "pipeline|explore|activity|health", layers: [{kind: "stage|path|url", value: string}]}`.
`format(route)` gives `#/pipeline/stage:discovery/url:https%3A%2F%2F...`. A `path` value is
the `/`-joined explore path.

---

### Task 1: Stages, history, hub

**Files:** Create `ccfeed/__init__.py`, `ccfeed/stages.py`, `ccfeed/history.py`, `ccfeed/hub.py`. Test: `tests/unit/dashboard/test_hub.py`, `tests/unit/dashboard/conftest.py` (puts `dashboard/` on `sys.path`).

- [ ] Write the tests:
  - `parse_ts` handles naive, `+00:00` and `Z` forms, plus datetime objects; it returns `None` for junk.
  - `freshness` boundaries: 119 s gives active; 120 s idle; 1800 s stale; `None` gives none.
  - `History.rate_per_min`: samples at t=0 (count 0) and t=30 (count 60) give 120/min. A drop in count clamps to 0.
  - History keeps at most 180 buckets; `series()` returns `[t, rate]` pairs.
  - With a `FakeFeed` and a fake clock, `Hub.step` builds snapshot stages with `rate_per_min`, `freshness` and `recent` (newest first, capped at 50).
  - A subscriber gets a `tick` with new events. A subscriber whose queue is full gets `snapshot` next, not a partial tick.
  - Backfill seeds the series.
  - A feed whose `poll` raises leaves the hub alive and puts `poll_error` in the snapshot.
- [ ] Run them and confirm they fail (`ModuleNotFoundError`).
- [ ] Implement.
- [ ] Run `pytest tests/unit/dashboard -q --no-cov` and confirm it passes. Commit.

### Task 2: Live table watcher

**Files:** Create `ccfeed/delta.py`. Test: `tests/unit/dashboard/test_delta_watcher.py`.

- [ ] Write the tests, using `write_deltalake` into `tmp_path` with the real on-disk column style (ISO string `_ingestion_time`, extra columns):
  - A missing path reports `missing`.
  - The first poll gives a count, no events, `recent_rows` up to 50, and a watermark.
  - An append gives exactly the new rows as events, with projected fields only.
  - A columns subset works when wanted columns are missing.
  - An overwrite or rewrite of old rows emits nothing old (watermark).
  - A data file removed after listing is skipped (Review Focus 1).
  - The per-poll cap of 500 keeps the newest rows.
  - A corrupt `_delta_log` gives `error` with the exception type.
- [ ] Implement `TableWatcher(path, wanted_columns, ts_column="_ingestion_time")` with:
  - `poll() -> WatchResult(status, count, version, last_write, new_rows, error)`
  - `recent_rows`
  - `read(columns, filter=None) -> list[dict]`, for queries
- [ ] Run the tests and confirm they pass. Commit.

### Task 3: Live queries and LiveFeed

**Files:** Create `ccfeed/queries.py` and `ccfeed/live.py`. Test: `tests/unit/dashboard/test_live_feed.py`.

- [ ] Write the tests:
  - Pure builders on row dicts: `build_stage_detail` per stage (depth bars, status codes, routing split, models, compression); `build_journey` skip rules (all 3 branches); `build_explore` (root hosts, child counts, `other` remainder beyond 200, `urls` at leaf, `depths` at root only).
  - `LiveFeed` over a temp lake with all five tables: poll gives five stages and events; `stage_detail("nope")` is `None`; `url_journey` for a known URL has done steps; an unknown URL gives `None`.
  - `explore(["example.com"])` works and an unknown path gives `None`.
  - `backfill` returns cumulative counts.
  - `services()` returns a delta row with tables found ("5/5").
- [ ] Implement. Results are cached per (table, version).
- [ ] Run the tests and confirm they pass. Commit.

### Task 4: Demo feed

**Files:** Create `ccfeed/demo.py`. Test: `tests/unit/dashboard/test_demo_feed.py`.

- [ ] Write the tests:
  - The same seed gives identical first 50 events; different seeds differ.
  - After warm-up, discovery runs at 40 to 120/min and analysis at 15 to 50/min, measured over 10 simulated minutes.
  - `depth` equals parent depth + 1.
  - Routing roughly matches: large docs 2 to 10% of analyzed.
  - Journeys follow the skip rules.
  - Explore root lists the demo hosts.
  - `services()` are all `ok` and say they are simulated.
  - `backfill` covers 30 minutes.
- [ ] Implement `DemoFeed(seed=7, clock=time.time, speed=1.0)`. It simulates on demand up to `now` in 1 s steps.
- [ ] Run the tests and confirm they pass. Commit.

### Task 5: serve.py routes and SSE

**Files:** Modify `dashboard/serve.py`. Test: `tests/unit/dashboard/test_serve_feed_routes.py`.

- [ ] Write the tests (a real server on an ephemeral port, as the existing tests do, with `DemoFeed` injected through `DashboardHandler.hub_factory`):
  - `/api/snapshot` has `mode` "demo" and five stages.
  - `/api/stage/discovery` returns 200; `/api/stage/x` returns 404.
  - `/api/url?u=javascript:alert(1)` returns 400; an unknown URL returns 404; a known URL returns 200.
  - `/api/explore?path=` returns 200.
  - `/api/stream` sends `retry:`, then `event: snapshot`, then `event: tick` within 3 s.
  - Closing the stream brings the subscriber count back to 0 within 2 s.
  - The stream cap returns 503.
  - Auth on: the new routes, stream included, return 401 without credentials.
  - `/package.json`, `/ccfeed/hub.py` and `/tools/smoke.py` return 404.
  - CSP `script-src` is `'self'` only.
- [ ] Implement:
  - `hub_factory` and `get_hub()`, lazy and locked.
  - The routes. The SSE loop uses `wfile.write` and `flush`, sends a ping every 15 s, and exits on `BrokenPipeError`/`ConnectionResetError`.
  - The CLI flags `--demo`, `--demo-seed`, `--delta-path` and `--interval`, with the env vars `CC_DEMO`, `CC_DEMO_SEED`, `DELTA_LAKE_PATH`, `CC_FEED_INTERVAL` and `CC_MAX_STREAMS`.
  - A live probe that builds on `_redis_from_env`, `queue_depths` and `fetch_upstream`.
  - `DENIED_NAMES = {"package.json"}`; add `tools` and `ccfeed` to `DENIED_DIRS`.
- [ ] Run all dashboard and serve tests and confirm they pass. Commit.

### Task 6: Vendor, pure client modules, shell (old UI removed)

**Files:**
- Create: `vendor/*`, `package.json`, `app/lib.js`, `app/router.js`, `app/format.js`, `app/state.js`, `app/layout/{squarify,budget,scale}.js`, `app/store.js`, `app/api.js`, `app/main.js`, `app/ui/{icons,nav,list,status,segmented,search,skeleton}.js`, `index.html`, `styles.css`, `manifest.json`, `icons/`.
- Delete: the old UI files and the old tests.
- Tests: `tests/{router,format,state,squarify,budget,scale}.test.js`.

- [ ] Download Preact 10 (latest 10.x), hooks and htm 3.1.1 with `npm pack` into a scratch dir. Copy the `dist/*.module.js` files. Rewrite `from"preact"` to `from"./preact.module.js"`. Append the license texts to `vendor/LICENSES.txt`.
- [ ] Write the node tests:
  - router: round-trip of every tab and layer kind; garbage hash gives the pipeline root; `parent()`; titles.
  - format: `compact` (999, 1.2K, 3.4M), `rate`, `relativeTime` (just now, 45s, 3m, 2h, 3d), `splitUrl` (host/path; non-http gives `{host: null}`), `isHttpUrl`, `bytes`, `freshnessLabel`.
  - state: `applySnapshot`, `applyTick` (event cap 500, series replace/append, dedupe by id), `filterEvents(kind, query)`.
  - squarify: total area preserved; every rect inside the bounds; empty input gives `[]`.
  - budget: per-edge 6/s and total 60 caps over a fake clock.
  - scale: `niceTicks`, `linear`, `monotonePath` with 0 or 1 point.
- [ ] Implement:
  - The pure modules.
  - The store: an EventSource with states connecting, live, reconnecting and offline (3 failures), plus stale tracking.
  - The app shell: tab bar, sidebar at ≥900 px, layer outlet, view transitions with push/pop direction, focus on h1, `document.title`.
  - `styles.css`: tokens, materials, components, dark mode, reduced motion, contrast, forced colors.
  - Views as placeholders to be filled by Tasks 7 to 9. Each tab renders its large title and a skeleton.
- [ ] Update the two serve tests that name old assets: `index.html` has no inline script, and every local script it references is served with a JS content type.
- [ ] Run `node --test Scraping_project/dashboard/tests/` and all Python dashboard and serve tests, and confirm they pass. Commit.

### Task 7: Pipeline view

**Files:** Create `app/ui/flow.js`, `app/ui/chart.js`, `app/views/pipeline.js`, `app/ui/eventrow.js`.

- [ ] Build:
  - The flow diagram: grid nodes, a measured SVG edge overlay, and particles driven by `budget.js`, paused on hidden or reduced motion.
  - The area chart: monotone path, gradient, 3 ticks, scrubbing by pointer and keyboard, `sr-only` summary.
  - The segmented control for the throughput stage.
  - Live rows and the health summary row.
- [ ] Add a node test for `chart.js` helpers if any pure logic leaves `scale.js`.
- [ ] Manual check with `serve.py --demo` in Playwright at the iPhone 15 viewport (393×852), light and dark. Commit.

### Task 8: Stage and URL journey views

**Files:** Create `app/views/stage.js`, `app/views/url.js`, `app/ui/bars.js`, `app/ui/timeline.js`.

- [ ] Build:
  - The stage view: tiles, chart, breakdowns, and recent rows merged with live events (dedupe by URL and time); refetch on version change at most every 5 s.
  - The URL view: timeline, Open Page (http(s) only), Share or Copy, the Found on link.
  - Error and Retry cards, and a 404 state.
- [ ] Manual check: Pipeline, then Discovery, then a URL, then back, on iPhone and desktop. Commit.

### Task 9: Explore, Activity, Health views

**Files:** Create `app/views/explore.js`, `app/views/activity.js`, `app/views/health.js`, `app/ui/treemap.js`.

- [ ] Build:
  - Explore: treemap and list, search filter, depth histogram, leaf URL list.
  - Activity: segmented filter, search, pause with the "N new" pill, a throttled live region.
  - Health: sections from the snapshot, plus the stream state from the store.
- [ ] Manual check at both viewports. Commit.

### Task 10: Smoke tool, CI, compose, docs

**Files:**
- Create: `tools/smoke.py`.
- Modify: `.github/workflows/ci-dashboard.yml` (syntax-check `app/**/*.js`; node 20), `.github/workflows/ci-dashboard-visual.yml` (run the smoke test and upload screenshots), `docker-compose.yml` (dashboard: mount the Delta volume read-only, `DELTA_LAKE_PATH`, `REDIS_HOST=redis`, `METRICS_UPSTREAM=http://prometheus:9090/metrics`).
- Rewrite: `README.md` and `A11Y_CHECKLIST.md`.

- [ ] Write `smoke.py`: it starts `serve.py --demo` on a free port, then for each of iPhone 15 and desktop 1280×900, light and dark:
  - Assert there are no console errors and the pill text is "Demo".
  - Assert five flow nodes.
  - Click Discovery, check the h1 is "Discovery", click the first recent row, check a URL layer h1, then go back.
  - Check Explore drills one level, the Activity filter changes the row count, and Health lists services.
  - Save screenshots to `dashboard/visual/`.
- [ ] Run the smoke test locally and confirm it passes.
- [ ] Run the whole suite: Python dashboard and serve tests, node tests, and `docker compose config` for the compose change. Commit.

### Task 11: Live check against real local data, then review and triage

- [ ] Run `serve.py --delta-path Scraping_project/data/delta_lake` against the local 500 MB lake. Check counts against `num_records` sums, check that missing tables show "No data", and time `/api/explore` and `/api/stage/discovery`.
- [ ] Do a whole-branch review with a fresh reviewer subagent. Fix the findings.
- [ ] Generate the triage list of the 28 open PRs and 68 issues that target the old dashboard, for the owner. Close nothing.
