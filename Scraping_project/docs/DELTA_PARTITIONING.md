# Delta Lake partitioning and declared tables

Applies to #261, #236, #268 and #433. The code lives in `src/lakehouse/lakehouse_manager.py`, and the settings are in `config.yml` under `delta_lake`.

## Partition keys per table

`delta_lake.partitions` maps each table to its partition columns. A table that isn't listed, or is mapped to `[]`, is unpartitioned. The manager fills in two **derived** columns on every write and merge:

| Column | Value |
|---|---|
| `domain` | Public-suffix-aware registrable domain of `url` (#251). If a row has no usable host, it goes to `domain_quarantine` (#458). |
| `date` | `YYYY-MM-DD` of the first usable field in `date_partition_source_fields` (by default `scraped_at_utc`, `discovered_at`, `processed_at`, `created_at`, `_ingestion_time`). If none are usable, it falls back to today's date (UTC). |

`date` uses the same column name and format as the `date` partition in `kafka-delta-ingest` (`--transform 'date: substr(scraped_at_utc, 0, 10)'`). That means Python writers and the Rust ingest agree on the layout of any table they share.

If a row already carries a valid `YYYY-MM-DD` `date`, that value is kept. A missing or invalid value is replaced with the derived day. A partition column can't be null or garbage.

Default layout for new lakes:

| Table | Partitions | Why |
|---|---|---|
| `stage1_discovery`, `stage2_page_analysis` | `domain` | Per-site reads and repairs (#458) |
| `stage1_errors`, `stage2_errors`, `stage3_summaries`, `stage4_summaries`, `stage4_large_doc_summaries`, `metadata_queue` | `date` | Append-only; retention, backfill and replay prune by day |
| `seed_urls`, `stage2_queue`, `js_spider_queue`, `stage4_large_docs` | none | Upserted by key with status changing in place. Partitioning by status would rewrite files on every transition, and these tables stay small. |

## Existing tables keep their layout

Partitioning is fixed when a table is **created**. delta-rs rejects a write whose `partition_by` differs from the table's ("Specified table partitioning does not match"), and it can't repartition in place, not even with `mode="overwrite", schema_mode="overwrite"`. So for each write and merge the manager:

1. reads the existing table's partition columns from `_delta_log` (cached per process), and
2. uses those, **not** the configured ones, logging a warning once and setting `delta_partition_config_drift{table}=1` when they differ.

Editing `delta_lake.partitions` therefore only affects tables created afterwards. It never breaks writes to an existing lake. To see where the configuration and the lake disagree, run `LakehouseManager(...).partition_report()`. It returns `{table: {"configured", "existing", "drift"}}`.

## Migrating an existing table

Do this offline, one table at a time:

1. Stop the writers for the table, or scale the stage to 0.
2. Copy the data into a new table that has the configured layout:
   ```python
   from deltalake import DeltaTable, write_deltalake
   from src.lakehouse.lakehouse_manager import LakehouseManager, infer_table

   lm = LakehouseManager(start_workers=False)
   src = lm.get_table_path("stage3_summaries")
   rows = DeltaTable(str(src)).to_pyarrow_table().to_pylist()   # chunk with to_batches() for large tables
   lm._enrich_records("stage3_summaries", rows, ["date"])       # derive `date`
   write_deltalake(str(src) + "__new", infer_table(rows), partition_by=["date"])
   ```
3. Check row counts (`DeltaTable(...).to_pyarrow_dataset().count_rows()`) on both tables.
4. Swap the directories (`mv stage3_summaries stage3_summaries__old && mv stage3_summaries__new stage3_summaries`) and restart the writers. `delta_partition_config_drift` should drop to 0.
5. After the retention window, delete `__old`.

## Retention by day

With a `date` partition, retention can delete whole days without scanning the table:

```python
DeltaTable(path).delete("date < '2026-07-01'")   # drops whole partitions (file removes only)
```

Then run `VACUUM` (`vacuum_all_tables`, honouring `delta.deletedFileRetentionDuration`) to reclaim the files. `metadata_queue` is now a declared table, so vacuum and compaction cover it.

## Declared tables (#433)

A table is *declared* if it's listed in any of:

- the manager's built-in table list,
- `delta_lake.tables`,
- `delta_lake.partitions`,
- `delta_lake.extra_tables`,
- the internal quarantine tables.

Writes and merges to any other name are *undeclared*. Shadow tables like that have no retention or partition policy. The handling depends on `delta_lake.undeclared_tables` (overridden by the `DELTA_UNDECLARED_TABLES` env var):

- `warn` (default): log once per table and count `delta_undeclared_table_writes_total{table,action="warned"}`.
- `reject`: refuse the write. `write()` returns `False` and `merge_into()` returns `-1`, and the counter is incremented with `action="rejected"`. Use this in production once the counter stays at 0 under `warn`.
