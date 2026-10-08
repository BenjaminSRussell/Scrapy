# Pipeline Control Center dashboard

Static dashboard served by `serve.py` (port 8080). Metrics are fetched from the
Prometheus-format endpoint on port 9090 **of the host the page was loaded from**
(`http://<host>:9090/metrics`), so it also works from another machine or container.
Override it with `?metrics=http://exporter:9090/metrics` (http/https only) or by setting
`window.CC_METRICS_URL` before `app.js` loads (#141).

All throughput figures are per minute, computed from the real time between samples (#141).
Labelled series such as `errors_total{stage="stage1"}` are kept under their full key and
also summed under the bare name (`errors_total`) unless an unlabelled series of that name
exists (#365).

```bash
python Scraping_project/dashboard/serve.py
# open http://localhost:8080  (styleguide: /styleguide.html)
node --test Scraping_project/dashboard/tests/   # helper unit tests
```

- Version watermark: `CC_VERSION` env or `git describe` via `/version.js`.
- Contributor styleguide: [`styleguide.html`](styleguide.html)
- Accessibility checklist for PRs: [`A11Y_CHECKLIST.md`](A11Y_CHECKLIST.md)

## Threat model notes (#1046)

**Activity log XSS.** Activity messages may contain text derived from metrics,
URLs, or error strings. Every message is HTML-escaped (`escapeHtml`) before
insertion; only `http(s)://` URLs are then linkified (`safeLinkify`) with
`rel="noopener noreferrer"`. `javascript:` and other schemes are never linked.
Do not introduce `innerHTML` writes of unescaped data.

**Metrics origin.** The dashboard trusts whatever `METRICS_URL` returns (including a
`?metrics=` override, so only open dashboard links you trust).
Values are parsed numerically (`parseMetrics`) and rendered via `textContent`,
so a hostile metrics endpoint can lie about numbers but cannot inject markup.
Keep the metrics endpoint on localhost / a trusted network; `serve.py` sends
`Access-Control-Allow-Origin: *` for local dev only — do not expose it publicly.

**Feature flags / env.** `window.__CC_FEATURES__` and `__CC_ENV__` are UI hints,
not authorization. Anything sensitive must be enforced server-side.
