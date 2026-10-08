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

All throughput figures are per minute, computed from the real time between samples (#141).
Labelled series such as `errors_total{stage="stage1"}` are kept under their full key and
also summed under the bare name (`errors_total`) unless an unlabelled series of that name
exists (#365).

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

## Connection state in the tab title (#945)

The browser tab title is prefixed with the live state so a background tab still signals trouble:

| Title prefix | Meaning |
|---|---|
| `● ONLINE` | metrics endpoint reachable (and `pipeline_running` is 1 or not reported) |
| `○ OFFLINE` | endpoint reachable but it reports `pipeline_running 0` |
| `⚠ ERROR` / `⚠ ERROR (N failed)` | the metrics fetch failed; N counts consecutive failures |

The topbar status is derived from the same state (`connectionState()` in `format-utils.js`).

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
