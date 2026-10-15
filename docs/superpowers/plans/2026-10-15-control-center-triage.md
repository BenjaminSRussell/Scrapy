# Control Center redesign: open PRs and issues to triage

This compares what was open on 2026-10-15 against branch `feat/control-center-redesign`.
Nothing has been closed or commented on; every decision below is yours.

**Something is still opening PRs against the old dashboard.** All 64 open PRs that
touch `Scraping_project/dashboard/` come from this account, and most were opened on
2026-10-14 and 2026-10-15. They edit `app.js`, `format-utils.js` and `index.html`,
which this branch deletes or replaces. Once the redesign merges, every one of them
will conflict. If an agent or automation is working through the `dashboard` issues,
pause it before merging.

## Issues the redesign resolves (close when it merges)

| Issue | Why it's done |
|---|---|
| #148 Finish Control Center: API-backed ops dashboard | `/api/snapshot`, `/api/stream`, stage, URL and explore APIs read Delta Lake and Redis directly |
| #384 Stage badges from live metrics | Stage freshness (active, idle, stale, no data) comes from each table's newest write |
| #527 Empty-state copy for all-zero charts | Charts say "Not enough data yet"; missing tables read "No data yet" and are never 0 |
| #536 Skip Chart.js updates for inactive tabs | Chart.js is gone; only the current layer renders |
| #579 Activity log innerHTML rebuild | Preact renders the lists; there is no `innerHTML` |
| #585 Extract inline CSS | Everything is in `styles.css`; CSP is `style-src 'self'` |
| #594 SRI for the Chart.js CDN | No CDN: Preact and htm are vendored |
| #944 Favicon | `icons/icon.svg`, plus home-screen icons |
| #946 Dark mode | Follows the system appearance |
| #947 Port and product copy | Health links Grafana via `CC_GRAFANA_URL`; the README documents the ports |
| #951 Configurable UConn branding | The UI has no institution branding; only the demo's hostnames are UConn-like |
| #959 Topbar metrics on tablet widths | The topbar is gone; there is a sidebar from 900 px |
| #961 Activity empty state | Activity has empty states for no events and for no matches |
| #964 Colorblind-safe charts | Stages are told apart by glyph and text, not hue alone; each chart is one series |
| #965 K/M axis ticks | Chart ticks use K formatting |
| #968 Meta description and theme-color | Both are in `index.html` |
| #970 Footer with version and links | The version is in the sidebar and on Health; Health links to Grafana and the raw JSON |
| #971 One activity source with filters | One store feeds Pipeline, Stage and Activity; Activity has filters and search |
| #979 Connection details | Health shows the stream state, last update and data source; the pill is always visible |
| #981 Responsive typography | rem type ramp plus Dynamic Type on iOS |
| #984 Escape metric-derived strings | All text goes through Preact |
| #985 Sticky topbar shadow repaint | The topbar is gone |
| #987 Per-card loading and error states | Every stage card and layer has loading, empty, unreadable and not-found states |
| #988 rel=noopener on external links | Every new-tab link has `rel="noopener noreferrer"` |
| #989 Overview grid stacking order | The overview grid is gone |
| #990 Directory listing and file exposure | Already on main (#721); this branch also denies `ccfeed/`, `tools/` and `package.json` |
| #991 Design tokens | `styles.css` `:root` tokens: system colors, type ramp, materials |
| #992 Button styles | `.btn`, `.btn.filled`, `.icon-btn` |
| #1004 Offline banner | Reconnecting and Offline banners and pill; a watchdog catches silent streams |
| #1019 Copy metrics snapshot JSON | Health links the raw `/api/snapshot`. Keep the issue if you want a copy button |
| #1024 Badge variants including unknown | Status labels: Normal, Degraded, Down, Unknown, Active, Idle, Stale, No data |
| #1039 CSP compatible with local Chart.js | No Chart.js; CSP is `script-src 'self'` |

## Issues still open after the redesign

| Issue | Status |
|---|---|
| #960 Locale-aware formatting | Partly done: dates use the browser locale, numbers use en-US |
| #969 serve.py forces no-store on static assets | Still true; PR #1470 addresses it (rebase needed) |
| #973 Confirm before leaving mid-error | Suggest closing: nothing is lost by leaving |
| #974 Print stylesheet | Not done |
| #980 Grafana empty home | Grafana-side; PR #1449 covers it (mostly non-UI) |
| #993 #994 #995 #996 #998 #999 #1000 #1001 #1023 Job-control UI (forms, toasts, dialogs, job list, drawer, start, stop, profile picker, history) | Not done: crawl controls were a non-goal and need the job API (#147). Rebuild them on the new components when that exists |
| #1002 Queue depth visualization | Partly done: Health shows Redis queue depth as text |
| #1003 Error-rate sparkline | Not done: Discovery shows its error count but no sparkline |
| #1005 Service-worker shell cache | A spec non-goal; keep it if you want offline reads |
| #1006 #1007 i18n catalog and switcher | Not done |
| #1008 Keyboard shortcut overlay | Not done |
| #1012 #1013 Performance budgets | Not measured on the new client. Re-scope: one SSE snapshot on connect, then small ticks |
| #1016 Density modes | Suggest closing: Dynamic Type and the iOS layout replace it |
| #1018 Chart PNG export | Not done |
| #1021 Coach marks | Not done |
| #1026 RTL stress test | Not done; the new CSS mostly uses physical properties |

## PRs worth porting (logic still applies; rebase onto the new serve.py or UI)

| PR | Note |
|---|---|
| #1470 Stop forcing no-store on static assets | serve.py only; fixes #969 |
| #1466 Static files extension allowlist | serve.py only; main already has an allowlist, so check what it adds |
| #1459 Hashed API keys for control routes | serve.py plus `cc_auth.py`; backend only |
| #1495 Read-only DuckDB SQL endpoint over Delta | serve.py plus backend files; no UI |
| #1449 Next steps on an empty Grafana home | Mostly Grafana provisioning; only the README conflicts |
| #1393 Free/Team/Enterprise plan catalog | A separate `plans.html` page; needs a link from the new UI |
| #1493 Stage 1 dual confirm for `ignore_robots` | A separate confirm page plus serve.py; the backend part ports |
| #1475 Operator audit log | The serve.py part ports; its UI targets the old `app.js` |
| #1500 rustmapper degraded-mode copy | The docs port; its UI copy targets old files |
| #1424 OIDC settings stub, #1465 chaos game-day panel, #1481 energy badge, #1486 branding | Mostly old-UI code; port the backend bits if wanted |

## PRs the redesign supersedes (suggest closing)

These change only the old UI, and the redesign either already does what they do or
replaces the screen they edit:

#1375 #1394 #1395 #1400 #1402 #1405 #1407 #1413 #1415 #1416 #1417 #1418 #1419 #1420
#1421 #1422 #1423 #1425 #1426 #1427 #1429 #1432 #1434 #1437 #1438 #1441 #1442 #1443
#1444 #1446 #1447 #1448 #1450 #1451 #1452 #1453 #1454 #1456 #1458 #1460 #1461 #1464 #1467
#1469 #1471 #1476 #1479 #1483 #1492 #1499 #1504

Some of them belong to issues that stay open: job controls, i18n, print, shortcuts,
PNG export, coach marks and the sparkline. If you want those features, re-implement
them on the new UI rather than rebasing these PRs.
