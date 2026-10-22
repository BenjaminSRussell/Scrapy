# SEV1: lake or log volume out of space (#561)

Operator checklist for a full or inode-exhausted volume under the Delta lake (`delta_lake.base_path`, or `DELTA_LAKE_PATH`). Writers fail closed below the watermarks, and readiness fails at the same point, so a full disk shows up as refused writes rather than silent `ENOSPC` loss.

Watermarks live in [config.yml](../../config.yml) under `delta_lake`: `disk_min_free_percent` and `disk_min_free_inodes_percent`, both `5` by default. Writers refuse a batch below them and do not retry or spill it, because a spill would also need the full volume. Readiness (`python -m src.utils.probe ready-disk`) fails the same way. Liveness does not check disk, so a full disk does not restart pods.

## Alerts

| Alert | Fires when | Severity |
|---|---|---|
| `DeltaVolumeLow` | Free space or free inodes below 10% for 5 min. Above the 5% refusal watermark, so there is still room to act. | warning |
| `DeltaVolumeWritesRefused` | Any `delta_write_failures_total{outcome="refused"}` in 10 min. Writes are being refused now. | critical (SEV1) |

Gauges: `lakehouse_volume_free_percent`, `lakehouse_volume_free_inodes_percent`. They refresh on every write and once a second while the maintenance worker is idle.

Logs: `[DISK] refusing <rows> rows for <table>: <reason>` at ERROR. Count these to size the gap.

## 1. Confirm and size the problem

```bash
df -h "$DELTA_LAKE_PATH"          # bytes
df -i "$DELTA_LAKE_PATH"          # inodes
du -sh "$DELTA_LAKE_PATH"/* | sort -h | tail -20
python -m src.utils.probe ready-disk; echo "exit=$?"
```

Note the largest consumers. Inode exhaustion usually means many small files (spill files, DLQ entries, or un-vacuumed Delta versions), not large data.

## 2. Stop the bleeding

- Pause the producers that write the largest tables if the volume will not recover within the hour. Refused batches are lost unless re-run (see step 5).
- Do not delete `_delta_log/` or any `*.parquet` by hand. That corrupts the table. Use the commands below.

## 3. Reclaim, in this order

Run each step as a dry run first. Every command here is preview-by-default except where noted.

1. **Crawl artifacts (logs, cache, temp):**
   ```bash
   python cli.py data gc --ttl-days 14 --verbose          # report
   python cli.py data gc --ttl-days 14 --apply            # delete expired files
   ```
2. **Scrapy HTTP cache:** lower the quota so `HttpCacheQuota` prunes oldest entries (`httpcache_max_bytes` in `config.yml`, pruned to `httpcache_prune_target_ratio` of it). Pruning runs at spider open, every `httpcache_prune_interval_secs`, and at close.
3. **Stage queue history:**
   ```bash
   python cli.py queue-gc --dry-run
   python cli.py queue-gc                                  # archives to <table>_history, then vacuums
   ```
4. **Delta vacuum (old file versions, keeps time-travel within retention):**
   ```bash
   python cli.py lake-vacuum                               # preview
   python cli.py lake-vacuum --apply                       # delete files past their retention tier
   ```
   Tiers are in `delta_lake.table_retention_hours`. Do not lower a tier to free space during an incident without a decision from the data owner.
5. **Logs under `./logs` (bind-mounted to `/app/logs` in compose):** the service logs to stdout, so rotate what is on the host. Inspect first:
   ```bash
   find logs -type f -mtime +7 -size +10M -print
   ```
   Compress or remove the files you confirm are not needed for an incident. Keep the last 24 hours of logs for the incident timeline.

After each step, check `python -m src.utils.probe ready-disk`.

## 4. Resume

1. Confirm `ready-disk` exits 0 and `lakehouse_volume_free_percent` is above 10.
2. Replay batches that were spilled before the outage. Replay deletes a file only after its write succeeds:
   ```bash
   python -c "from src.lakehouse.lakehouse_manager import LakehouseManager as L; print(L.get_instance().replay_spilled_writes())"
   ```
   Inspect `_write_spill/` first. A failed replay leaves the file in place.
3. Restart or resume the paused producers.
4. Watch `DeltaVolumeWritesRefused` clear and `delta_write_failures_total{outcome="refused"}` stop increasing.

## 5. Recover refused writes

A refused batch is not in the lake and not in `_write_spill/`. Recover it from the source:

- Find the window from logs: `grep "\[DISK\] refusing" <log>`. Each line gives the table and row count.
- Re-run the producer for that window, or re-process the affected queue rows, once the volume is healthy. Writes are idempotent for the merge-keyed tables (`merge_into`); append-only tables may need a dedup check on re-run.

## 6. Follow-up

- Record the peak usage and what filled the volume. If a stage writes unbounded data, add it to `data gc` or a retention tier.
- Decide whether the watermarks and the 10% alert are right for the volume size. Keep the alert above the refusal watermark.
- Related: [Redis capacity](../REDIS_CAPACITY.md) for queue memory, [Pipeline SLOs](../guides/PIPELINE_SLOS.md), [kill switch](sev1_abuse_kill_switch.md) for the SEV1 format this page follows.
