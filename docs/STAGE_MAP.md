# Pipeline stage map

Canonical end-to-end stage map for the Scrapy crawl pipeline. It complements
[docs/ROADMAP.md](ROADMAP.md)
([#370](https://github.com/BenjaminSRussell/Scrapy/issues/370)), which is the
shared product sequence and not a stage-by-stage map. This file is the in-repo
copy of
[#606](https://github.com/BenjaminSRussell/Scrapy/issues/606).

Stages are only the five #606 names: **1 Scout**, **2**, **3**, **4**, and
**Orch**. This file does not change runtime behavior and does not fix the
linked issues. The failure modes below are open gaps. Whether a cited issue
is still open is the tracker, not this file:
<https://github.com/BenjaminSRussell/Scrapy/issues>.

Entry paths are the ones #606 names. They live under `Scraping_project/`
(for example `Scraping_project/src/stage1/scout_spider.py`).

## Stages

| Stage | Entry | Inputs | Outputs | Failure modes (open gaps) |
|---|---|---|---|---|
| **1 Scout** | `src/stage1/scout_spider.py` (+ experimental deep_dive/js/depth) | seeds, sitemap_parser, future rustmapper | dicts → `stage2_queue` / `js_spider_queue`; Redis seen | Missing `BaseSpider` (#376/#610); SeedManager stub (#607); QueueItemPipeline **not** in ITEM_PIPELINES (#608); compose `src.workers.*` (#142/#434); settings≠config (#151) |
| **2** | `src/stage2/stage2_worker.py` | `stage2_queue` pending | `stage2_page_analysis`; routes to `stage4_large_docs` | DeltaHelper API (#609); empty queue if Stage 1 never writes |
| **3** | `src/stage3/stage3_worker.py` | quality `stage2_page_analysis` | writes **`stage4_summaries`** (vs const `stage3_summaries`) | naming (#612); DeltaHelper; #521 #528 |
| **4** | `src/stage4/stage4_worker.py` | **should** drain `stage4_large_docs` (#611); actually filters stage 2 `is_massive_doc` | `stage4_large_doc_summaries` | PDF queue orphans |
| **Orch** | `src/orchestrator/pipeline_orchestrator.py` | stages 1→2→(3\|4) | PipelineStats | partial success exit (#521) |

## Flow

The diagram is the flow #606 recorded. It is not a claim that the path runs
today.

```mermaid
graph LR
  Seeds --> Scout --> S2Q[stage2_queue] --> S2 --> S2A[stage2_page_analysis]
  Scout --> JSQ[js_spider_queue]
  S2 --> S4Q[stage4_large_docs]
  S2A --> S3 --> Sum[stage4_summaries]
  S4Q --> S4 --> LSum[stage4_large_doc_summaries]
  Rust[rustmapper] -.-> Seeds
```

Stage 3's arrow lands on `stage4_summaries`, the table #606 says the worker
writes, not the `stage3_summaries` constant. That split is open gap #612.
Stage 4's arrow from `stage4_large_docs` is the drain #611 says the worker
should do. #606 says the worker instead filters stage 2 on `is_massive_doc`,
so the PDF queue can be left orphaned. Neither gap is fixed here.

## Infrastructure

Not extra stages. Open gaps #606 names around the five stages:

- **Redis:** compose OK; dual `REDIS_URL` vs `HOST` (#511).
- **Kafka:** KafkaPipeline + config; **not in** main compose (#145);
  crawler-entrypoint **requires** Kafka (#615).
- **kafka-delta-ingest:** Rust → Delta (#531 / #544 / #535).
- **Delta tables:** `seed_urls`, `uconn_urls`, `stage1_*`, `js_spider_queue`,
  `stage2_queue`, `stage2_page_analysis`, `stage3_*`, `stage4_*`.
- **Path drift:** compose `DELTA_LAKE_PATH=/data/delta` vs code
  `./data/delta_lake` (#613).
- **Metrics:** `scrapy_prometheus` :9410; metrics exporter and Control Center
  not in compose (#616); Grafana :3000 (#157).

## Happy path vs what #606 calls broken

**Ideal, not delivered here:** compose core + streaming → rustmapper / scout →
SeedManager → stage 2 → 3/4 → Control Center + Grafana; the orchestrator
fails closed.

**Broken, recorded as open gaps:** no `src.main` / no `src.workers`
(#142 / #434); Scout `BaseSpider` (#610); SeedManager stub (#607);
QueueItemPipeline unregistered (#608); DeltaHelper API (#609); Kafka
entrypoint (#615); settings ≠ config (#151).

## Rust-sitemap (`rustmapper`) → Stage 1

#562 and #565 are already filed. #606 recommends a `discovery_backend` config
key, a `rustmapper_bridge.py` plus `Crawler` / JSONL, feeding SeedManager, and
a Stage 1-only pin. That recommendation is not implemented in this file.
Prerequisites it names remain open gaps: #607, #610, #608, #142.

Shared handoff codes (`TIMEOUT`, `ROBOTS_DENY`, `HTTP_429`, `PARSE_FAIL`,
`PARTIAL`, `OK`) and the Stage 1 retry table are in
[discovery_error_taxonomy.md](discovery_error_taxonomy.md) (#933).

## Recorded finish order

Order #606 recorded. Each item is an open gap. This file does not schedule
or close them.

1. #142 / #434 entrypoints
2. #607, #610, #608, #609
3. #145, #511, #613, #615
4. #151, #467, #521
5. #562, #565
6. #148, #147, #157, #542, #616

## Atomic follow-ups #606 filed

| Issue | Gap #606 named |
|---|---|
| #607 | Restore SeedManager — `seed_manager.py` is a circular stub |
| #608 | Register QueueItemPipeline in `ITEM_PIPELINES` |
| #609 | Align DeltaHelper with the LakehouseManager API |
| #610 | ScoutSpider also imports missing `base_spider` |
| #611 | Stage4Worker should consume `stage4_large_docs` |
| #612 | Unify the Stage 3 summary table name |
| #613 | Align `DELTA_LAKE_PATH` compose env |
| #615 | crawler-entrypoint Kafka wait optional |
| #616 | metrics-exporter + Control Center compose services |
| #617 | Remove the dead `get_postgres()` stub from scout |

Already open when #606 was filed, and not re-filed by it: #142 / #434
entrypoints, #376 experimental BaseSpider, #145 compose unify, #151 / #467
settings, #521 orchestrator partial success, #562 / #565 rustmapper, #511
Redis URL, #157 Grafana JSON, #542 HEALTHCHECK.

---

*Source: #606. Sync with #370 / [docs/ROADMAP.md](ROADMAP.md). Update this file when that stage map changes.*
