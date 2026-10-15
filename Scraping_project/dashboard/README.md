# Pipeline Control Center dashboard

Static dashboard served by `serve.py` (port 8080). The browser only talks to that
origin: metrics come from **`/api/metrics`**, which `serve.py` fetches server-side
from `METRICS_UPSTREAM` (default `http://127.0.0.1:9090/metrics`) (#400). That also
works when the page is opened via a LAN IP or hostname. Override per page with
`?metrics=<http(s) url>` or `window.CC_METRICS_URL` (#141). A cross-origin override
must also be allowed in the CSP with `CC_CONNECT_SRC` (see below). A `?metrics=` value
that is not http(s) is ignored, and the dashboard says so in the activity log and
console (#910). Opened as a `file://` page (no server), it falls back to
`http://localhost:9090/metrics`.

The activity log keeps its last 50 entries in `sessionStorage` for this browser
tab (#1029). A reload restores them and shows a note that they came from before
the reload and may be stale. Closing the tab drops them; Clear drops them
immediately.

All throughput figures are per minute, computed from the real time between samples (#141).
Labelled series such as `errors_total{stage="stage1"}` are kept under their full key and
also summed under the bare name (`errors_total`) unless an unlabelled series of that name
exists (#365).

## Metrics flood (#690)

A high-cardinality scrape makes full chart history noise and burns CPU on a modest
NOC browser. After each scrape the dashboard counts exposition series (one sample
line each; the summed bare name is not an extra series) and times `parseMetrics`.
If either value is strictly over its threshold, charts, sparklines, and chart data
tables are hidden and only the numeric KPI counters keep updating. A banner names
the degraded mode. Charts come back after N consecutive samples under both limits.
One sample over either limit resets that streak.

| Knob | Default | Meaning |
|---|---|---|
| `window.CC_METRICS_FLOOD.maxSeries` or `?floodMaxSeries=` | `2000` | Series count that trips flood |
| `window.CC_METRICS_FLOOD.maxParseMs` or `?floodMaxParseMs=` | `50` | Parse time (ms) that trips flood |
| `window.CC_METRICS_FLOOD.recoverIntervals` or `?floodRecover=` | `3` | Calm intervals before charts return |

Query parameters override `window.CC_METRICS_FLOOD`. Non-numeric or negative values
fall back to the defaults. `floodRecover` must be at least 1. Example:
`window.CC_METRICS_FLOOD = { maxSeries: 500, maxParseMs: 30, recoverIntervals: 5 }`.

```bash
python Scraping_project/dashboard/serve.py
# open http://localhost:8080  (styleguide: /styleguide.html)
node --test Scraping_project/dashboard/tests/   # helper unit tests
```

- Bind address (#735): `127.0.0.1:8080` by default. Override with `--host/--port` or
  `CC_HOST`/`CC_PORT`. A wildcard bind (`--host 0.0.0.0`, e.g. inside a container) must be
  explicit and prints a warning.
- Only dashboard assets are served (#721): `.html/.js/.css/images/fonts/.json/.map/.txt`.
  Missing files, directory listings, dotfiles, `tests/` and source/docs (`serve.py`, `*.md`)
  return a plain 404 that never includes filesystem paths.
- Version watermark: `CC_VERSION` env or `git describe` via `/version.js`.
- Redis queue depths (#401): the System tab polls `/api/queues`, which reads the keys in
  `CC_QUEUE_KEYS` (comma list, default `js_spider:priority_queue`) with the type-aware
  length (`LLEN`/`ZCARD`/`SCARD`/`XLEN`/`HLEN`, so a list matches `redis-cli llen`).
  Connection: `REDIS_URL`, else `REDIS_HOST`/`REDIS_PORT`/`REDIS_DB`/`REDIS_PASSWORD`.
  If Redis is down the API returns 503 and the panel says "unavailable"; it never
  shows made-up zeros. Stage 2–4 backlogs are Delta tables / Kafka topics, not Redis
  keys; see the Grafana pipeline-health dashboard for those.

## Server settings

| Variable | Default | Purpose |
|---|---|---|
| `CC_HOST` / `CC_PORT` | `127.0.0.1` / `8080` | Bind address (#735) |
| `METRICS_UPSTREAM` | `http://127.0.0.1:9090/metrics` | Exporter fetched by `/api/metrics` and checked by `/api/health` (#400) |
| `CC_UPSTREAM_TIMEOUT` | `4` | Seconds per upstream fetch |
| `CC_QUEUE_KEYS` | `js_spider:priority_queue` | Redis keys shown by `/api/queues` (#401) |
| `CC_AUTH_TOKEN` | unset (auth off) | Shared secret: `Authorization: Bearer <token>`, or the password in the browser login prompt (any user name) (#189/#455) |
| `CC_BASIC_AUTH` | unset (auth off) | `user:password` for the browser login prompt (HTTP Basic) |
| `CC_CORS_ORIGINS` | unset (no CORS headers) | Comma list of exact origins allowed to read responses cross-origin; `*` is for local development only and prints a warning (#189) |
| `CC_CONNECT_SRC` | unset | Extra http(s) origins allowed in CSP `connect-src` (only for a cross-origin `?metrics=`) (#245) |
| `CC_RATE_LIMIT` / `CC_RATE_BURST` | `600` / `120` | Per-client token bucket: requests per minute and burst. Excess requests get `429` with `Retry-After`. `CC_RATE_LIMIT=0` disables it (#257) |
| `CC_ENV` | unset | Dashboard environment. Chaos game-day buttons require exactly `staging`. `ENV` or `APP_ENV` of `production` or `prod` keeps them off even if `CC_ENV=staging` (#967) |
| `CC_CHAOS_GAMEDAY` | off | `1` / `true` / `yes` / `on` arms the two stubs. Any other value, including a typo, stays off (#967) |

## Exposing the dashboard (#189/#455)

Auth is **off by default** because the default bind is loopback (solo local use).
To share it on a LAN for a demo:

```bash
CC_HOST=0.0.0.0 CC_BASIC_AUTH='ops:a-long-random-password' python Scraping_project/dashboard/serve.py
# browsers get a login prompt; scripts: curl -H "Authorization: Bearer $CC_AUTH_TOKEN" ...
```

A wildcard bind without `CC_AUTH_TOKEN`/`CC_BASIC_AUTH` prints a warning. The gate is a
shared-secret stub, not an identity provider, and Basic credentials are only as safe as the
transport. For anything beyond a trusted LAN, keep `serve.py` on loopback and publish it
through a reverse proxy or ingress that terminates TLS and authenticates users (for example
nginx/Traefik with OAuth2 Proxy, or a Kubernetes ingress with an auth annotation). Set
`CC_CORS_ORIGINS` only if another site really needs to read the API.
- Contributor styleguide: [`styleguide.html`](styleguide.html)
- Accessibility checklist for PRs: [`A11Y_CHECKLIST.md`](A11Y_CHECKLIST.md)

## Overview screenshot (#1044)

One smoke capture of the Overview tab, for CSS refactors. It is not a pixel-diff
gate (fonts and Chrome builds differ), and it is not part of `node --test`.

| | |
|---|---|
| Artifact name | `dashboard-overview-screenshot` |
| File inside the artifact | `overview.png` |
| What it shows | `#tab-overview` (full page, Overview selected) after one frozen metrics sample. Viewport 1280×900, UTC, Chart.js CDN blocked, animations off, auto-refresh paused. The sticky top bar and refresh bar are captured in normal flow so a full-page shot does not pin them to the viewport. |
| Where it is produced | Optional workflow [`.github/workflows/ci-dashboard-visual.yml`](../../.github/workflows/ci-dashboard-visual.yml): pull requests that touch this directory, or Actions → Run workflow. `continue-on-error: true`, so a capture failure does not fail the dashboard unit tests. The artifact is kept 14 days. It does not run on every push to `main`. |
| Regenerate locally | `python Scraping_project/dashboard/overview_screenshot.py --channel chrome` (or bundled Chromium after `python -m playwright install chromium`). Default path: `Scraping_project/dashboard/visual/overview.png` (gitignored). Needs `playwright==1.55.0`, already pinned for the JS spider. |

## Comparison mode (#1035)

The refresh-bar toggle **Compare to previous hour** overlays a dashed baseline on the throughput, URLs, pages, and summaries line charts (not the stage bar or routing doughnut). The choice is remembered in `localStorage` (`cc.comparePrevHour`).

The metrics feed does not publish hour averages yet, so the overlay is a labeled placeholder: a flat zero dashed line, not a computed average. A finite gauge replaces that one series:

| Series | Gauge |
|---|---|
| URLs discovered | `stage1_urls_discovered_prev_hour_avg` |
| Pages analyzed | `stage2_pages_analyzed_prev_hour_avg` |
| Summaries created | `stage3_summaries_created_prev_hour_avg` |
| URLs/min | `stage1_urls_per_min_prev_hour_avg` |
| Pages/min | `stage2_pages_per_min_prev_hour_avg` |

## Grafana deep links (#1097)

Control Center does not embed Grafana. The page is served from this origin (`:8080`) and Grafana is another origin (`:3000` for the monitoring profile, `:3001` for the observability pack), so an iframe would fight the CSP and would not be a supported embed. Open the pack in the browser instead:

| Panel | URL |
|---|---|
| Lag, Redis depth, crawl rate, Delta commits | http://127.0.0.1:3001/d/ops-lag-depth-commits |
| Kafka consumer lag | http://127.0.0.1:3001/d/ops-lag-depth-commits?viewPanel=2 |
| Redis frontier depth | http://127.0.0.1:3001/d/ops-lag-depth-commits?viewPanel=3 |
| Crawl rate | http://127.0.0.1:3001/d/ops-lag-depth-commits?viewPanel=4 |
| Delta commits | http://127.0.0.1:3001/d/ops-lag-depth-commits?viewPanel=5 |

The same uid is provisioned on the monitoring-profile Grafana at http://localhost:3000/d/ops-lag-depth-commits. Redis depth on the System tab (`/api/queues`) is the live key length. The Grafana panel is `redis_queue_length` from StatsD. They are the same frontier, two views.

## Connection state in the tab title (#945)

The browser tab title is prefixed with the live state so a background tab still signals trouble:

| Title prefix | Meaning |
|---|---|
| `● ONLINE` | metrics endpoint reachable (and `pipeline_running` is 1 or not reported) |
| `○ OFFLINE` | endpoint reachable but it reports `pipeline_running 0` |
| `⚠ ERROR` / `⚠ ERROR (N failed)` | the metrics fetch failed; N counts consecutive failures |

The topbar status is derived from the same state (`connectionState()` in `format-utils.js`).

## Metric name map (#1038)

The System tab Settings panel lists every Prometheus series the page reads and the UI field (element id) it fills. The list is `METRIC_UI_MAP` in `format-utils.js`, rendered by `renderMetricNameMap`. `app.js` does not keep a second copy. Session uptime and the last-refresh clock are not series. The Stage 4 overview spark follows `stage3_summaries_created_total` (the same sample buffer as the Stage 3 spark), not `stage4_large_doc_summaries_total`.

## Danger sound (#1037)

The Activity log has a **Sound on danger** checkbox. It is unchecked until an operator turns it on, and that choice is stored in `localStorage` under `cc.dangerSound`. When it is on, a new `danger` activity row plays a short tone (Web Audio, no audio file). The same message is not chimed again inside the screen-reader repeat window. Success, warning, info, and timeout rows never play a sound. `prefers-reduced-motion: reduce`, or a `prefers-muted` media preference, keeps the page silent even if the box is checked.

## Scrape targets (#1052)

The Overview card **Scrape targets** ranks hosts from the metrics sample already
fetched for the rest of the page (`topScrapeTargets` in `format-utils.js`). It
reads only these families, using a `host` label or else a `domain` label:

`scrape_targets`, `pipeline_scrape_targets`, `seed_hosts`, `urls_by_host`,
`urls_by_domain` (a `_total` suffix is fine).

The same function accepts an API stub, `{ targets: [{ host, value }] }` or
`{ hosts: [...] }`, so a later route can supply the list without a second
parser. It does not call that route and it does not scrape. The configured
`domain` label on crawl counters is not a request host and is ignored.
Sentinels (`unknown`, `rejected`, `-`) are dropped. When no usable series is
present the card stays on **No scrape-target metric reported.** and does not
invent seeds. Host text is rendered with `textContent`.

## Chaos game day (#967)

The System tab card **Chaos game day** productizes the safe half of the chaos scenarios in issue #250 (Redis down, Kafka stalled). It does not run those tests and it does not touch a broker.

| Control | What it does |
|---|---|
| Kill Redis (stub) | `POST /api/chaos/gameday` `{"action":"kill-redis"}` records the request. Redis stays up. |
| Pause Kafka (stub) | Same route, `{"action":"pause-kafka"}`. Kafka stays up. |
| Postmortem template | [`chaos-gameday-postmortem.txt`](chaos-gameday-postmortem.txt), linked from the card. |

Both buttons are `hidden` and disabled in the page. `GET /api/chaos/gameday` shows them only when `enabled` is true: the effective environment is exactly `staging` and `CC_CHAOS_GAMEDAY` is on. The browser check is a hint. The POST handler applies the same rule and answers `403` otherwise. An unknown action is `400`. Neither path opens a Redis or Kafka client. `effect` is always `none`.

`ENV=production` or `APP_ENV=prod` disables the card even if `CC_ENV` says staging. A flag typo does not arm it.

## Threat model notes (#1046)

**Activity log XSS.** Activity messages may contain text derived from metrics,
URLs, or error strings. Rows are built with DOM APIs only (`textContent`,
`setAttribute`), never `innerHTML` (#906): a `<script>` in a message renders as
text. Only `http(s)://` URLs become links (`splitLinks`), with
`rel="noopener noreferrer"`; `javascript:` and other schemes are never linked.
Do not introduce `innerHTML` writes of unescaped data.

**CSP and headers (#245).** `serve.py` sends an enforcing
`Content-Security-Policy`: `default-src 'self'`, scripts only from this origin and
jsDelivr (Chart.js, pinned with an SRI `integrity` hash), `connect-src 'self'`,
`object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'none'`. Inline `<script>` is
not allowed (feature flags live in `features.js`). Inline style attributes are, via
`style-src 'unsafe-inline'`, until they move to CSS classes. It also sends
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`,
`Permissions-Policy` and `Cross-Origin-Opener-Policy: same-origin`. Check with:

```bash
curl -sI http://127.0.0.1:8080/ | grep -iE 'content-security|x-frame|nosniff|referrer|permissions'
```

**Metrics origin.** The dashboard trusts whatever `METRICS_URL` returns (including a
`?metrics=` override, so only open dashboard links you trust).
Values are parsed numerically (`parseMetrics`) and rendered via `textContent`,
so a hostile metrics endpoint can lie about numbers but cannot inject markup.
Keep the metrics endpoint on localhost / a trusted network. `serve.py` sends no
CORS headers unless `CC_CORS_ORIGINS` is set.

**Feature flags / env.** `window.__CC_FEATURES__` and `__CC_ENV__` are UI hints,
not authorization. Anything sensitive must be enforced server-side.
