# Redis queue and seen-set capacity

Planning sheet for queue depth, seen-set memory, crawl rate, replica count, and Delta growth (#264). Numbers below are either already in this repo (cited) or formulas. This document does not record a measured URLs/sec, a measured bytes-per-million seen URLs, or a measured Delta GB/day. Those were not measured. Do not fill the formulas with a guessed constant.

Nothing here changes runtime. `admit_enqueue`, `maxmemory`, the eviction policy, and `QueueBacklog` stay as they are. Seen-set keys stay `site:{site_id}:{prefix}:urls`. There is no new member-count cap.

## What is already fixed

| Knob | Value | Where |
|---|---|---|
| Queue-depth soft limit | 10000 | `QueueBacklog` is `max by (queue) (redis_queue_length) > 10000` for 10 minutes (`monitoring/alerting_rules.yml`). `DEFAULT_QUEUE_HIGH_WATERMARK` is `10_000` (`src/utils/redis.py`). `REDIS_QUEUE_HIGH_WATERMARK` defaults to that (`.env.example`). |
| Low watermark | 8000 | `DEFAULT_QUEUE_LOW_WATERMARK` is `8_000`. `REDIS_QUEUE_LOW_WATERMARK` defaults to that. `admit_enqueue` keeps the low watermark strictly below the high one. |
| Backpressure | `block` | `DEFAULT_BACKPRESSURE_MODE`. `shed` is the other mode. This doc does not change either. |
| Seen-set key | `site:{site_id}:{prefix}:urls` | `seen_set_key()` in `src/utils/redis.py`. Example: `site:uconn:seen:urls`. No TTL. No `SCARD` cap. |
| Eviction | `volatile-lru` | Compose `--maxmemory-policy`, Helm `redis.config.maxmemoryPolicy`, `k8s/deployment.yaml`. TTL-less seen-sets and queues are not evicted. At `maxmemory`, Redis rejects writes. |
| Memory SLO | 80% of `maxmemory` | `RedisHighMemory`: `redis_memory_used_bytes / (redis_memory_max_bytes > 0) > 0.8` for 5 minutes. README "Redis memory policy". |
| Chart / Compose `maxmemory` | `2gb` | Helm `redis.config.maxmemory`, Compose `--maxmemory 2gb`, `k8s/deployment.yaml` `--maxmemory 2gb`. Container limit on the chart Redis is `2Gi` (`redis.resources.limits.memory`). |
| Chart Redis replicas | 1 | `templates/redis-statefulset.yaml` sets `replicas: 1`. AOF can recover data after the node returns, and it does not keep the queue available during node loss (#216). |
| Production Redis | external | `values-prod.yaml`: `redis.enabled: false`, `redis.external.host: redis.example.edu`. Clients use `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`. Do not set `REDIS_URL`. |

The chart does not set `maxmemory` on the external server. Use that server's own `maxmemory` in the formulas. The 10000 / 8000 watermarks are process defaults, not Helm Redis config, so production keeps them unless the process env overrides them.

Helm writes the same soft limits as comments and as `redis.capacity` in `k8s/helm/scraping-pipeline/values.yaml`. No template reads `redis.capacity`.

## Queue depth

The Redis queue `QueueBacklog` and the watermark actually watch is the JS render priority queue. `JSPriorityQueue.size()` is `ZCARD` of the queue key (default `js_spider:priority_queue`). `monitoring/metrics_exporter.py` gauges that depth as `redis.queue.length` with the label `queue="priority_queue"` (StatsD renames it to `redis_queue_length`). The update method's comment says this is the only Redis-backed queue left. The exporter also looks for a `message_queue_config` attribute and would emit 0 for those names, but `Config` has no such attribute, so the series that moves is `priority_queue`. `config.yml` `message_queues` is not that gauge. Stage 2/3/4 work queues are Delta tables (`stage2_queue` and the other tables the exporter lists). `redis_queue_length` is not their depth, and 10000 is not a limit on those tables.

`admit_enqueue` compares that depth to the watermarks (`src/utils/redis.py`):

- Depth greater than 10000: backpressure starts. `block` sleeps until depth is at or under 8000, bounded by `REDIS_QUEUE_BACKPRESSURE_MAX_BLOCK_SECONDS` (default 5). `shed` refuses priorities below `REDIS_QUEUE_SHED_BELOW_PRIORITY` (default 50) immediately. Higher priorities still block.
- After a trip, depth still above 8000 stays in backpressure until it falls to the low watermark. A depth of 10000 exactly is not greater than the high watermark.
- `QueueBacklog` is the page for that same line, held for 10 minutes. The 10 minutes is the alert `for` duration, not a measured drain time.

A second, separate bound already exists and is not this soft limit: `stage1.js_queue_max_size` is 100000 (`config.yml`; `JSPriorityQueue.DEFAULT_MAX_SIZE`). Past that, the lowest-priority members are dropped. Evicted URLs stay in `<queue>:hashes`, so that claim set can keep growing after the sorted set is capped. This document does not change `max_size`, the hash set, or `admit_enqueue`.

Lag, not a fitted curve (consume rate was not measured):

```
lag_seconds ≈ redis_queue_length / consume_rate_urls_per_sec
```

`consume_rate_urls_per_sec` was not measured. Read a live rate from the worker counters once a crawl is running (`scrapy_stage_urls_processed_total` and `scrapy_stage_batch_seconds_total` in `src/utils/metrics_sink.py`). Do not publish that quotient as a capacity number until it has been measured for the site you are sizing.

Queue memory was not measured either:

```
queue_bytes ≈ ZCARD * (mean_utf8_bytes(url) + zset_per_member_overhead) + companion_key_bytes
```

Companion keys are `<queue>:enqueued_at` (sorted set), `<queue>:hashes` (set, kept after eviction), and `<queue>:metadata` (hash of JSON, only when metadata was stored). `mean_utf8_bytes` and `zset_per_member_overhead` were not measured. Before using a constant, run `MEMORY USAGE` on each key and divide by `ZCARD` or `SCARD`. This repository has no such sample.

## Seen-set size

`RedisHelper.mark_url_seen` / `claim_url` `SADD` the canonical URL (`canonical_or_raw` in `src/utils/url_canon.py`) into `site:{site_id}:{prefix}:urls`. The id is the argument, else `SITE_ID`, else `TENANT_ID`, else `REDIS_SITE_ID`, else `default`. Each prefix is its own set. Two profiles that share an id share a set.

These keys have no TTL, so `volatile-lru` will not drop them. Filling `maxmemory` makes `SADD` fail, and the seen store then fails closed (`SeenStoreUnavailable`) unless `REDIS_SEEN_FAIL_MODE=open`.

**Soft limit (planning only).** Keep Redis `used_memory / maxmemory` under the existing `RedisHighMemory` line, 0.8. With the chart and Compose `maxmemory` of `2gb`, that line is 80% of 2gb for all Redis memory, not a seen-set-only budget. `redis.capacity.seenSetSizeSoftLimit` in the Helm values records that sentence. `seenSetMemberCap` is null: there is no `SCARD` cap in `RedisHelper`, and this document does not add one.

Bytes, not measured:

```
seen_set_bytes(key) = MEMORY USAGE key
bytes_per_member(key) = MEMORY USAGE key / SCARD key    # only when SCARD > 0
```

A bytes-per-million figure was not measured. Do not invent one. `MAX_URL_LENGTH` is 2048 in `src/utils/validation.py` and `src/core/constants.py`: `url_rejection_reason` returns `too_long` when `len(url) >= 2048`. That is a character ceiling on URLs the validator accepts, not a measured average length and not a bytes-per-URL.

Other keys share the same 80% budget. They are not the seen-set: the JS queue and its companion keys (above), `circuit:{domain}` (those have a TTL), and discovery claim keys `seen:{job}:{site}:{url_hash}` (`config.yml` `stage1.discovery`, TTL `claim_ttl_seconds` 604800). Claim keys are not `site:{site_id}:{prefix}:urls`. Under `volatile-lru` a key with a TTL can be evicted; a seen-set cannot.

`maxmemory` is Redis's data cap. The chart also sets a 2Gi container memory limit. Process overhead above `used_memory` was not measured. If the cgroup OOM-kills the pod while `used_memory` is still under `maxmemory`, measure RSS against `used_memory` before raising either knob. This document does not change `redis.config.maxmemory` or the container limit.

The chart Redis PVC is `redis.persistence.size` 10Gi. That is an allocation for the AOF (and RDB, if one is written), not a measured bytes-per-day. AOF rewrite needs extra space on that volume; how much was not measured.

Connection count is a separate, already published model. `DEFAULT_MAX_CONNECTIONS` is 50 (`src/utils/redis.py`). The README ("Redis connection pool sizing") states `sum over processes (REDIS_MAX_CONNECTIONS) <= 0.8 × Redis maxclients`, and that Redis's default `maxclients` is 10000. This document does not change the pool.

## URLs/sec per replica

Not measured. Configured caps are not a throughput:

```
urls_per_sec_per_replica ≈ in_flight / mean_response_seconds

in_flight <= min(concurrent_requests, n_hosts * concurrent_requests_per_domain)
```

`mean_response_seconds` was not measured. Stage 1 records `scrapy_response_time_seconds` (`src/scrapy_prometheus.py`) once a crawl is running. Dividing a concurrency cap by `download_delay` is not a measured URLs/sec: `download_delay` is a floor between requests, and the response time sits on top of it.

Caps already in `config.yml` (limits, not measurements):

| Profile | `concurrent_requests` | per host | `download_delay` | autothrottle target |
|---|---|---|---|---|
| `stage1.spiders.scout` | 1024 | 16 | 0.01 | 8.0 |
| `stage1.spiders.deep_dive` | 32 | 8 | 0.25 | 2.0 |
| `stage1.depth_spider` | 64 | (spider default) | (not set here) | (not set here) |

Stage 2: `max_workers` 100, `per_host_concurrency` 4, `batch_size` 50. Stage 3: `max_workers` 50, `batch_size` 100. Stage 4: `max_workers` 1. Helm replica counts are in the next section.

AutoThrottle, robots `Crawl-delay` (capped at `ROBOTS_MAX_CRAWL_DELAY`, 60s), `Retry-After` (capped at `RETRY_AFTER_MAX_DELAY`, 120s), and soft-ban slot delay (`SOFT_BAN_SLOT_DELAY`, 30s) only reduce the rate. See the README sections "Rate limits and per-domain concurrency" and "robots.txt, Crawl-delay and Retry-After".

```
stage_urls_per_sec ≈ rate(scrapy_stage_urls_processed_total{stage}[5m])
```

That rate was not measured for this document.

## Replicas per seed size

Not a measured recommendation. The formula, once `urls_per_sec_per_replica` has been measured for that site:

```
replicas ≈ ceil(seed_count / (urls_per_sec_per_replica * target_seconds))
```

Until then, start from the counts already configured. They are not throughputs.

`DEPLOYMENT.md` "Worker Scaling" (quick-start note): Stage 1: 1 instance (I/O bound); Stage 2: 2–5; Stage 3: 1–2; Stage 4: 1. Same page, "Resource Allocation": CPU 0.5–1.0 cores, memory 1–2 GB, storage 100+ GB for Delta Lake per worker. Those are the guide's figures, not a benchmark.

The Helm chart is the supported install (`DEPLOYMENT.md`). Its current defaults (`values.yaml`):

| Workload | `replicaCount` | HPA |
|---|---|---|
| `scrapyApp` | 3 | on, min 3, max 10, CPU 75%, memory 80% |
| `stage2Worker` | 3 | on, min 2, max 8, CPU 70%, memory 75% |
| `stage3Worker` | 2 | on, min 1, max 6, CPU 70%, memory 75% |
| `stage4Worker` | 1 | none (`enabled: false`; scale with `replicaCount`) |
| Redis | 1 | none. Production does not run this pod. |

HPA in `templates/scrapy-hpa.yaml` scales on CPU and memory utilization. It does not scale on `redis_queue_length`. Stage 2 can also scale on the Prometheus gauge `stage2_queue_pending` (external metric or the KEDA example in `values-stage2-queue-hpa.example.yaml`, #653). That gauge is the Delta `stage2_queue` pending count, not `redis_queue_length`. `QueueBacklog` tells a person to scale the consuming stage or look for a stall. Raising replicas does not raise Redis `maxmemory`. One Redis (chart replica 1, or the single external primary) holds every seen-set and the JS queue.

`scrapyApp.env.SCOUT_INSTANCES` is `"8"`. That is a configured env value, not a measured replica requirement.

## Delta growth per day

Not measured. Do not publish a GB/day.

```
net_bytes_per_day ≈ bytes_appended_per_day - bytes_vacuumed_per_day
```

Both terms were not measured. Allocations already in the repo are reservations, not growth rates:

| Allocation | Size | Where |
|---|---|---|
| Shared Delta PVC | 500Gi | `deltaLake.persistence.size` |
| Scrapy app Delta PVC | 200Gi | `scrapyApp.persistence.deltaLake.size` |
| Quick-start note | 100+ GB | `DEPLOYMENT.md` "Resource Allocation" |

Retention bounds how long removed files stay. It is not a growth rate (`config.yml` `delta_lake`):

- `retention.log_retention`: `interval 30 days`
- `retention.deleted_file_retention`: `interval 7 days` (vacuum does not delete unreferenced files younger than this)
- `queue_retention_hours`: 168 for completed/failed rows in `stage2_queue`, `js_spider_queue`, and `stage4_large_docs`

Row size on disk depends on the Parquet encoding of each table and was not measured. `MEMORY USAGE` style sampling for Delta is the table directory size divided by the row count from `python cli.py health`, on a lake you already have. This repository does not record that quotient.
