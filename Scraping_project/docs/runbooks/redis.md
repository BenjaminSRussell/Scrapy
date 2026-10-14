# Redis failover and seen-set recovery

Operator checklist for a Redis outage, a full memory reject, or a lost seen-set (#547). Nothing here changes runtime. Connection, key names, watermarks, eviction, and the chart stay as they are on this tree.

Sizing formulas, not incident steps, are in [Redis capacity](../REDIS_CAPACITY.md). The three published SLOs stay as they are in [Pipeline SLOs](../guides/PIPELINE_SLOS.md): Stage 2 success, Redis queue depth (`QueueBacklog`), and Kafka lag. This page does not retune them. `monitoring/alerts.yml` is not a Prometheus rule file and is not recreated. No image size is stated here.

Related pages: [DEPLOYMENT.md](../../DEPLOYMENT.md) (capacity and the Redis notes), [k8s Redis availability](../../k8s/README.md), [monitoring](../guides/MONITORING.md), [data usage](../guides/DATA_USAGE.md), [kill switch](sev1_abuse_kill_switch.md).

## Connection

Processes use `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, and `REDIS_DB` (`src/utils/redis_env.py`). Defaults are `localhost`, `6379`, unset password, database `0`. Helm injects `REDIS_PASSWORD` from the `redis-credentials` Secret. An unset password logs a warning and is local dev only.

| Install | What the process gets |
|---|---|
| Compose | Host `redis`, port `6379`. Password is `REDIS_PASSWORD` when set. |
| Chart, external host empty | StatefulSet still renders, `replicas: 1`. `REDIS_HOST` is `<release>-redis`, port is the Service port `6379`. |
| Production overlay | `redis.enabled: false`, `redis.external.host: redis.example.edu`, `redis.external.port: 6379`. `REDIS_HOST` / `REDIS_PORT` are that host and port. |

`redis.capacity` in [values.yaml](../../k8s/helm/scraping-pipeline/values.yaml) is documentation only. No template reads it. Changing `queueDepthSoftLimit`, `queueDepthLowWatermark`, or `seenSetMemberCap` does not move `admit_enqueue`, `maxmemory`, or `QueueBacklog`.

## Keys

| Key | What it is | TTL | Evicted by `volatile-lru`? |
|---|---|---|---|
| `site:{site_id}:{prefix}:urls` | The seen-set. `RedisHelper.mark_url_seen` / `claim_url` `SADD` the canonical URL (`canonical_or_raw` in `src/utils/url_canon.py`). Example: `site:uconn:seen:urls`. | none | no |
| `seen:{job}:{site}:{url_hash}` | Discovery claim (`stage1.discovery`, shared-dedup). Not the seen-set. | `claim_ttl_seconds` `604800` | yes |
| `cbstate:{digest}` / `cbfail:{digest}` | Shared circuit-breaker state and failure count (`src/utils/redis.py`). | recovery window (default `60` s) | yes |
| `circuit:{domain}` | Legacy domain block. `open_circuit` default `900` s. | yes | yes |
| `js_spider:priority_queue` | JS render sorted set. `ZCARD` is `redis_queue_length{queue="priority_queue"}`. Companions: `:enqueued_at`, `:hashes`, `:metadata`. | key has none; members older than `stage1.js_queue_ttl_seconds` (`86400`) are dropped by the app | key no |
| `{spider}:url_hashes` | `BaseSpider._deduplicate_urls`. Members are `url_canon.url_hash` values, not URLs. Names in tree: `scout:url_hashes`, `deep_dive:url_hashes`, `depth:url_hashes`. | none | no |

`site_id` is the argument, else `SITE_ID`, else `TENANT_ID`, else `REDIS_SITE_ID`, else `default`. Each prefix is its own set. The helper default prefix is `seen`. `config.yml` does not pick the prefix.

Discovery claims do not replace seen-sets. `config.yml` says the same. `discovery_dedup.url_hash` hashes the raw URL string. `url_canon.url_hash` hashes the canonical URL. Those two hashes are not interchangeable. Do not copy one key onto the other.

`CIRCUIT_BREAKER_SHARED=0` (also `false` / `no` / `off`) keeps the breaker in that process. The default is shared. A down Redis does not add a connect timeout to every request: the shared store cools down for `5` s and the process keeps its own memory (`src/utils/retry.py`).

Stage 2, 3, and 4 work queues are Delta tables. `redis_queue_length` is not their depth.

## Symptoms

Check these before changing anything.

| You see | It means |
|---|---|
| `RedisDown`: `up{job="redis"} == 0` for `1m` | The `redis` job scrapes the exporter, not port `6379` (Compose `redis-exporter:9121`, chart `<release>-redis-exporter:9121`). Confirm with `probe ready-redis` or `PING` before restarting the server. The exporter can be down while Redis still answers. |
| Workers unhealthy: `redis <host>:<port> not answering PING` | `REDIS_HOST` is set and `python -m src.utils.probe ready-redis` failed. `-NOAUTH` still counts as up. |
| `RedisSeenStoreFailingClosed`: `sum by (op) (rate(redis_seen_check_errors_total[5m])) > 0.5` for `10m` | Seen-store errors. Admission pauses. Crawl stalls. It does not recrawl. |
| Log `Redis seen-store <op> failed; failing CLOSED` or `SeenStoreUnavailable` | Same fail-closed path. |
| `RedisPoolExhausted`: `rate(redis_pool_exhausted_total[5m]) > 0` for `5m` | Every pooled connection was busy for `REDIS_POOL_TIMEOUT` (default `2` s). Pool size default `REDIS_MAX_CONNECTIONS` `50`. |
| `RedisHighMemory`: `redis_memory_used_bytes / (redis_memory_max_bytes > 0) > 0.8` for `5m` | Over 80% of `maxmemory`. Writes will be rejected at 100%. Seen-sets are not evicted. |
| Log `OOM command not allowed` | At `maxmemory` with nothing evictable left. `SADD` / enqueue fail. Seen store fails closed. |
| Log `Redis maxmemory-policy is allkeys-lru` (also `allkeys-lfu`, `allkeys-random`) | `check_eviction_policy` on connect. TTL-less seen-sets and queues can disappear. The crawl starts over. |
| `QueueBacklog`: `max by (queue) (redis_queue_length) > 10000` for `10m` | JS priority queue over the depth line. `for: 10m` is the alert hold, not a measured drain time. |
| `redis_queue_backpressure_total{queue,action}` climbing, log `over the high watermark` | `admit_enqueue` is in backpressure. Default mode `block`. |
| `CircuitBreakersOpen` (`circuit_breaker_open_count > 0` for `10m`) | Domains are blocked. Severity info. Not a Redis outage by itself. |
| Pods cannot open `REDIS_PORT`, Redis itself is up | Production NetworkPolicy is default-deny and cannot match a DNS name. `redis.external.egressCIDRs` must be the endpoint CIDR. The overlay's `203.0.113.0/24` is the documentation range (TEST-NET-3), not a live network. |
| Every URL looks new, `SCARD` of the seen-set is `0`, no `RedisSeenStoreFailingClosed` | The set is gone (volume loss, flush, or an `allkeys-*` eviction). Fail-closed did not run, because Redis answered. Stop crawls before they refill the web. |

`python cli.py health` lists Delta row counts. It does not check Redis.

## Metrics

Rules are in [alerting_rules.yml](../../monitoring/alerting_rules.yml). Grafana: pipeline health, and SLO panels at `http://localhost:3000/d/pipeline-slos`.

```promql
up{job="redis"}
redis_memory_used_bytes / (redis_memory_max_bytes > 0)
max by (queue) (redis_queue_length)
sum by (op) (rate(redis_seen_check_errors_total[5m]))
sum by (op) (rate(redis_pool_exhausted_total[5m]))
sum by (queue, action) (rate(redis_queue_backpressure_total[5m]))
circuit_breaker_open_count
circuit_breaker_state
```

`admit_enqueue` and `QueueBacklog` are the same comparison: depth `> 10000`. A depth of `10000` exactly does not trip. The producer high watermark is `REDIS_QUEUE_HIGH_WATERMARK` (default `10000`), the low watermark is `REDIS_QUEUE_LOW_WATERMARK` (default `8000`), and `REDIS_QUEUE_BACKPRESSURE_MODE` defaults to `block`.

- `block` sleeps until depth is at or under `8000`, bounded by `REDIS_QUEUE_BACKPRESSURE_MAX_BLOCK_SECONDS` (default `5`). One exhausted block then sheds until the depth falls to the low watermark.
- `shed` refuses priorities below `REDIS_QUEUE_SHED_BELOW_PRIORITY` (default `50`) immediately. Higher priorities still block.
- After a trip, depth still above `8000` stays in backpressure. These env vars are process settings, not `redis.capacity`.

The series that moves is `queue="priority_queue"` (`JSPriorityQueue.size()`, `ZCARD` of `js_spider:priority_queue`). A separate cap, `stage1.js_queue_max_size` `100000`, drops the lowest-priority members. Dropped URLs stay in `js_spider:priority_queue:hashes`.

## Failover

There is no Sentinel client in this chart or in the workers. Clients do not follow a replica. They keep using `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, and `REDIS_DB`. AOF recovers data after the node returns. It does not keep the queue available during node loss.

### Chart Redis (external host empty)

[redis-statefulset.yaml](../../k8s/helm/scraping-pipeline/templates/redis-statefulset.yaml) renders only when `redis.enabled` is true and `redis.external.host` is empty. `replicas` is `1`. Compose is also one container (`redis:7-alpine`, AOF, `--maxmemory 2gb`, `--maxmemory-policy volatile-lru`). The chart image tag is `redis:7.4-alpine` with the same `maxmemory` and policy.

1. Do not scale this StatefulSet and expect a second copy. There is no failover.
2. Do not delete the PVC (`redis.persistence`, `10Gi`, mount `/data`). The queue and the seen-sets are unavailable until that same node and its volume are back.
3. The chart does not set `appendfsync`. Redis's default `everysec` applies: about one second of writes can be missing after replay. That sentence is the chart's RPO in [k8s/README.md](../../k8s/README.md), not a new setting.
4. If `redis.persistence.enabled` is false, the volume is `emptyDir` and the data is gone when the pod is gone. The default is enabled.
5. When the pod is Ready, `redis-cli PING` (the chart sets `REDISCLI_AUTH` from the Secret when auth is on). Then `CONFIG GET maxmemory-policy` must be `volatile-lru`, and `CONFIG GET maxmemory` must be `2gb` on this chart. Compose is the same policy and `2gb`.
6. `SCARD` the seen-set and `ZCARD js_spider:priority_queue` before letting crawls run. If both survived, do not rebuild.

### Production external endpoint

[values-prod.yaml](../../k8s/helm/scraping-pipeline/values-prod.yaml): `redis.enabled: false`, `redis.external.host: redis.example.edu`, port `6379`, `egressCIDRs: [203.0.113.0/24]`. `profile: production` refuses to render when the host is empty or `redis.enabled` is still true. Replace the host and the documentation CIDR before a real install.

Failover of that server is outside this repo. Point `REDIS_HOST` at a hostname that already reaches the current primary. Workers will not switch primaries by themselves. Password stays `REDIS_PASSWORD` in `redis-credentials`. `REDIS_DB` stays `0` unless the process sets it. The chart does not set `maxmemory` on the external server.

If pods time out and `PING` from outside the namespace works, fix `redis.external.egressCIDRs` before restarting Redis. `kubectl port-forward` does not go through NetworkPolicy.

### While Redis is down

- Seen-store calls fail closed (`REDIS_SEEN_FAIL_MODE` default `closed`). They raise `SeenStoreUnavailable`. They do not report the URL as unseen. Do not set `REDIS_SEEN_FAIL_MODE=open` in production. That is the alert action on `RedisSeenStoreFailingClosed`. Open mode treats a failed check as unseen, a failed mark as not recorded, and a failed claim as claimed.
- Discovery claims are not fail-closed. On a Redis error, `DiscoveryDedup` logs and uses a process-local dict. That does not refill `site:{site_id}:{prefix}:urls`.
- To stop downloads without Redis, set `CRAWL_KILL_SWITCH=1` and restart the workers. Steps are in the [kill switch runbook](sev1_abuse_kill_switch.md).
- Do not `FLUSHDB`. `RedisHelper.clear_all` refuses unless `confirm=True` or `ALLOW_REDIS_FLUSH=1`. A flush deletes seen-sets, queues, and circuit keys for every worker.

### `allkeys-lru`

Chart, Compose, and `k8s/deployment.yaml` set `volatile-lru`. Only keys with a TTL can be evicted. Seen-sets, `{spider}:url_hashes`, and the JS queue key have no TTL, so a full Redis rejects writes instead of dropping them.

`allkeys-lru`, `allkeys-lfu`, and `allkeys-random` evict those keys. The seen-set vanishes and the crawl starts over. `check_eviction_policy` logs an error on connect. Do not switch to an `allkeys-*` policy to free memory. Raise `maxmemory` from a measured `MEMORY USAGE`, or stop producers and let the JS queue drain. `RedisHighMemory` is the `0.8` warning. `seenSetMemberCap` is `null`: there is no `SCARD` cap.

## Seen-set recovery

Rebuild only when the volume is not coming back, or `SCARD` is `0` after AOF replay. A replay that still has the set does not need this. There is no `cli.py` subcommand that rebuilds Redis. Do not run `python cli.py reset` (that rewrites Delta and re-seeds).

### 1. Stop admission

With Redis up and the set empty, workers will treat URLs as new. Engage the kill switch before rebuilding:

```bash
cd Scraping_project
python cli.py killswitch on --reason "redis seen-set empty, ticket <id>" --actor "<your name>"
```

`python cli.py killswitch status` shows `"engaged": true`.

### 2. Confirm the key

```text
SCAN MATCH site:*:*:urls COUNT 100
SCARD site:<site_id>:<prefix>:urls
CONFIG GET maxmemory-policy
```

Compose, from `Scraping_project`: `docker-compose exec redis redis-cli ...`. If `REDIS_PASSWORD` is set, pass `-a "$REDIS_PASSWORD"`. On the chart pod, `redis-cli` picks up `REDISCLI_AUTH`.

The key you refill must be the one workers read. `mark_url_seen(url, "scout", site_id="uconn")` writes `site:uconn:scout:urls`, not `site:uconn:seen:urls`. A wrong prefix or site id builds a set nobody reads.

Unscoped keys from older releases (`seen:urls`, `scout:urls`, `{prefix}:urls`) are not read. `SEEN_KEY_MIGRATION` in `src/utils/redis.py` is a `RENAME` onto `site:{id}:{prefix}:urls` only when the new key is absent. It is not a Delta rebuild. `RENAME` replaces the destination if it already exists.

### 3. Rebuild `site:{site_id}:{prefix}:urls` from Delta

Delta is the durable URL list. `LakehouseManager.read` returns a list of dicts (it materializes the table). Filter, or export with `python cli.py export`, before reading a large lake. Tables that store a `url` column: `stage1_discovery`, `stage2_queue`, `seed_urls`, and the domain side table (`stage1.domain_urls_table`; the bundled `config.yml` name is `uconn_urls`). A missing table returns `[]`.

Run from `Scraping_project` with the workers' `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`, `DELTA_LAKE_PATH`, and site env (`SITE_ID` / `TENANT_ID` / `REDIS_SITE_ID`). Use `mark_url_seen`. It `SADD`s `canonical_or_raw(url)`. A raw `SADD` of the lake string, or `add_to_set`, does not canonicalize, so `/page`, `/page/`, and `?utm_*` variants miss each other.

`SADD` is idempotent. A second run does not duplicate members. `SCARD` can be lower than the row count because variants collapse. `mark_url_seen` raises `SeenStoreUnavailable` on a Redis error (fail-closed). Fix Redis and run again. Do not flip fail-open to finish the loop.

```python
from src.lakehouse.lakehouse_manager import LakehouseManager
from src.utils.redis import get_redis, seen_set_key

prefix = "seen"   # the prefix workers pass to mark_url_seen / claim_url
site_id = None    # None: SITE_ID, else TENANT_ID, else REDIS_SITE_ID, else default
tables = ["stage1_discovery", "stage2_queue", "seed_urls", "uconn_urls"]

lake = LakehouseManager(start_workers=False)
helper = get_redis()
print(seen_set_key(prefix, site_id=site_id))
calls = 0
for table in tables:
    for row in lake.read(table, columns=["url"]):
        url = row.get("url") if isinstance(row, dict) else None
        if not isinstance(url, str) or not url.strip():
            continue
        helper.mark_url_seen(url, prefix, site_id=site_id)
        calls += 1
print(calls, helper.get_set_size(seen_set_key(prefix, site_id=site_id)))
```

Change `prefix`, `site_id`, and `tables` to the crawl you are restoring. This does not write discovery claims, and it does not refill `{spider}:url_hashes`.

Stay under the `RedisHighMemory` line (`0.8`) while loading. At `maxmemory`, `SADD` is rejected. Chart and Compose `maxmemory` are `2gb`. An external server uses its own `maxmemory`.

### 4. Do not rebuild claims into the seen-set

`seen:{job}:{site}:{url_hash}` is a string key with a TTL, written by `DiscoveryDedup` (`SET NX EX`), not a set of URLs. Shared-dedup (the `config.yml` default, `job_id` `default`) uses that shape. Losing claims lets both discovery engines enqueue again until they claim again. Restoring claims does not restore `site:{site_id}:{prefix}:urls`. Do not `RENAME` or `SADD` claim keys onto the seen-set. Under `volatile-lru` a claim key can be evicted; a seen-set cannot.

### 5. Spider hash sets are not the seen-set

`ScoutSpider`, `DeepDiveSpider`, and `DepthSpider` extend `BaseSpider`. First-seen inside the spider is `SADD` of `url_hash(url)` into `{name}:url_hashes` (`scout`, `deep_dive`, `depth`). An empty hash set admits URLs that the seen-set already contains, so a site-set rebuild alone does not stop those spiders from treating URLs as new.

Refill that key only if `SCARD` shows it empty and that spider is what is recrawling. Members are the 16-hex `url_canon.url_hash`, not the canonical string and not `discovery_dedup.url_hash`. Same URL list as above. One key per spider. `JavaScriptSpider` does not use this key. It drains `js_spider:priority_queue`, then pending rows in the Delta table `js_spider_queue`. Rows that were never written to that table are not restored by a Delta read. This page does not rebuild the sorted set.

```python
from src.utils.redis import get_redis
from src.utils.url_canon import url_hash

spider_key = "scout:url_hashes"  # or deep_dive:url_hashes, depth:url_hashes
client = get_redis().client
# urls: the same http(s) strings you passed to mark_url_seen
client.sadd(spider_key, *(url_hash(url) for url in urls))
```

### 6. Verify

- `CONFIG GET maxmemory-policy` is `volatile-lru`. It is not `allkeys-lru`.
- `SCARD site:<site_id>:<prefix>:urls` is non-zero and stable across two reads.
- `SISMEMBER` of one URL you know was in the lake, using the canonical form (tracking parameters stripped, host lowercased). A raw lake string can miss.
- `SCAN` shows no reliance on `seen:urls` without a `site:` prefix.
- `sum by (op) (rate(redis_seen_check_errors_total[5m]))` is flat. `RedisHighMemory` is not firing.
- `up{job="redis"} == 1` and `python -m src.utils.probe ready-redis` exits `0`.
- `ZCARD js_spider:priority_queue` matches `redis_queue_length{queue="priority_queue"}`. `QueueBacklog` is still `> 10000` for `10` minutes. Do not silence it by editing the rule.
- `python cli.py health` still lists the Delta tables you read. The lake was not reset.
- Discovery claim keyspace (`seen:<job>:<site>:<hash>`) is not how you decide the seen-set is back.
- If you refilled a spider hash set, `SCARD scout:url_hashes` (or the spider you filled) is non-zero.

Then release the switch:

```bash
python cli.py killswitch off --actor "<your name>" --reason "seen-set restored from delta"
python cli.py killswitch audit --limit 20
```

Restart Stage 1 spiders the switch closed. Watch `redis_seen_check_errors_total` and `redis_queue_length` for the next few scrapes. A climbing seen-error rate means stop again. Do not answer it with `REDIS_SEEN_FAIL_MODE=open`.
