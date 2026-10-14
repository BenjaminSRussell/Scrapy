# Product roadmap

Canonical big-picture roadmap for finishing and modernizing
[BenjaminSRussell/Scrapy](https://github.com/BenjaminSRussell/Scrapy).
This file is the in-repo copy of
[#370](https://github.com/BenjaminSRussell/Scrapy/issues/370), synthesized from
the Scrapy UI and Scrapy Backend strategic plans. Discovery bots file atomic
work under phases `[P1]`–`[P4]`; implementers pull **foundation / correctness
first**.

This is the shared sequence, not a stage-by-stage map. Pipeline meta stays on
[#606](https://github.com/BenjaminSRussell/Scrapy/issues/606). Whether a cited
issue is still open is the tracker, not this file:
<https://github.com/BenjaminSRussell/Scrapy/issues>.

## North star

**Backend:** One config, durable queues, restartable stages, multi-site crawl
jobs via API, and observable health — not a one-shot script with broken Docker
entrypoints.

**UI:** An **ops cockpit** operators trust in an incident: *is the pipeline
alive, where is work stuck, what just changed, what can I do?* Calm control
room — honest states, keyboard-friendly, mobile-usable — not a static metrics
collage.

**Keep forever:**

- **Control Center (ops)** — live stage health, queues, activity, eventual
  start/stop
- **Grafana (analytics)** — history, PromQL, alerts

Never merge them; make the boundary obvious in copy and links.

## Shared sequence

Do not thrash this order.

| Phase | Outcome | Key themes / issues |
|---|---|---|
| **P0 Foundation** | `docker compose` boots Redis + one stage | Entrypoints (#142), packaging (#143), lean images (#144), unify compose (#145), settings/config (#151), repo hygiene (#150), CI (#149) |
| **P1 Correctness + Trustworthy glass** | Small crawl Stage1→4 without crashes; UI numbers never lie | Delta/worker contracts, stage bugs; UI #141 rates/units, #153 a11y, #154 XSS/charts, #155 mobile, #156 healthy defaults |
| **P2 Reliability + same-origin shell** | Kill mid-run and resume without silent loss; Control Center talks to a real API | Checkpoints, graceful shutdown, backpressure, DLQ; **#148** API-backed Control Center |
| **P3 Operable pipeline** | Create/pause/cancel jobs for ≥2 domains; dashboard not read-only | **#147** crawl job API + profiles + scheduling; UI job controls |
| **P4 Scale & finish** | Sustained multi-site crawl with alerts; demo/on-call polish | OTEL (#146), multi-replica, retention, SLOs; dark mode, shortcuts, Grafana provisioning (#157) |

## Must-have vs later

**Must (production-ready):**

- P0 + P1 + core P2 (resume, no drop-on-fail, exclusive queue claims)
- Honest online/offline/error/stale UI
- Same-origin metrics; one documented ops URL in compose
- A11y tabs + non-color status; usable on phone; no XSS in activity
- Clear ops vs analytics labeling

**Later:**

- Full React/design-system rewrite
- HA Redis, full OTEL, bronze/silver/gold layering
- Dark mode, digit shortcuts, advanced activity filters

## Hard rules

1. **UI must not invent a second control path** — job controls wait on the
   crawl-job API (#147).
2. **Sequence:** entrypoints → Delta contracts → orchestrator checkpoint →
   job API → Control Center controls.
3. **Dual config / dual compose** will keep breaking PRs until #145 / config
   unify land.
4. Prefer closing umbrella issues before filing micro-slices that duplicate
   them.

[ADR-0001](adr/0001-config-yml-is-canonical.md) (accepted) records
`Scraping_project/config.yml` as the canonical application configuration:
Python reads it through `get_config()`, Scrapy settings are derived from it,
and environment variables override it only for deployment-specific values.
That decision is not reopened here. Rule 3 still names compose unify (#145)
as #370 wrote it.

## Success checks

- **Backend:** Stage1→4 completes a small crawl; restart mid-run without silent
  data loss; API can drive ≥2 site profiles.
- **UI:** A new on-call answers in <30s from Control Center alone: pipeline up?
  hottest stage? last error? link to Grafana for history? — without DevTools or
  guessing ports.

## Related workstreams

Atomic issues are filed by lane bots (UI, Backend, Future, Test, Security,
Data, DX, Product) with `[P1]`–`[P4]` title prefixes.

The notes below were posted on #370 on 2026-09-18. They record what had been
filed then, and which priorities implementers were told to pull. Counts and
“still open” wording are that day’s snapshot.

### Silent-loss P1s

Filed and ready (skip duplicate #611):

- #645 `js_spider_queue` never drained
- #646 hop reconciliation + Stage3∥Stage4 barrier
- #647 dual discovery dedup + empty-vs-failed handoff (rustmapper ↔ Scout)

Also recorded then: Control Center future standards ~56; Rust-sitemap
integration standards ~20. Pipeline meta remains #606.

### Lane rollup (keep implementers on P0/P1)

**UI (Control Center trust/a11y/perf):** ~#328–#947 sparse (26+ filed at that
note). Umbrellas #141, #148, and #153–#157 still stood. Future owns Control
Center standards #665–#941; UI parking overlaps those numbers.

**Backend:**

- Core: #170, #313–#320, #325, #327, #329
- rustmapper integrate: #562, #565, #572, #574, #587, #592, #593
- Contracts: #948
- Build owns P0s #607–#610

**Future silent-loss (implementer-hungry):** #645, #646, #647

**Rust-sitemap standards:** Future #914–#941 (Backend was skipping duplicate
P3 drafts)

Do not flood micro-issues — pull the P0/P1 work above first.

### UI wave

**120 Control Center issues** were filed in that lane, numbered **#328–#1054**
(sparse / non-contiguous).

- Early umbrellas still stood: #141, #148, #153–#157
- That lane’s atomic backlog is trust, a11y, perf, and polish, titled
  `[P1]`–`[P4]`
- About 85 future-std/sitemap drafts that overlap Future’s #665–#941 and
  #914–#941 were parked and not re-filed

Prefer **[P1]** honesty, a11y, and metrics bugs first. Micro `[P3]` / `[P4]`
polish can wait.

---

*Source: #370. Update this file when that sequence changes.*
