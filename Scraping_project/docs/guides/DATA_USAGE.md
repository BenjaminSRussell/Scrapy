# Data usage guide (Delta Lake)

All pipeline data lives in Delta Lake tables, which are Parquet files plus a `_delta_log/` transaction log. The pipeline no longer uses SQLite.

## Where the data is

The lake root is `DELTA_LAKE_PATH` if that is set, otherwise `delta_lake.base_path` (default `./data/delta_lake`). Compose sets it to `/data/delta` inside the containers, which is `./data/delta` on the host. Each table is a directory under the root. Column contracts for `pages`, `extractions` and `jobs` are in the [Delta schema registry](../../../docs/contracts/README.md).

`delta_lake.tenant_path_prefix` is false, so the table directories below sit directly on that root. See [Per-tenant directory](#per-tenant-directory-optional-671) before changing it.

| Table | Written by | Contents |
|---|---|---|
| `seed_urls` | `cli.py seeds`, `scripts/reseed.py` (`python reseed.py` is deprecated), scout seed expansion | Seeds and their active flag |
| `uconn_urls` (= `stage1.domain_urls_table`) | Stage 1 | URLs on the allowed domains |
| `stage1_discovery` | Stage 1 | Every discovered URL. Partitioned by `domain` |
| `stage1_errors`, `stage1_offsite_candidates`, `js_spider_queue` | Stage 1 | Fetch errors, off-site links, JS render candidates |
| `stage2_queue` | Stage 1 → 2 | Work queue (`status`: `pending` / `completed` / `failed`) |
| `stage2_page_analysis` | Stage 2 | Page analysis (text stats, quality, links). Partitioned by `domain` |
| `stage2_errors` | Stage 2 | Analysis failures |
| `stage3_analytics`, `stage3_summaries` | Stage 3 | Summaries and analytics for normal pages |
| `stage4_large_docs`, `stage4_large_doc_summaries`, `stage4_summaries` | Stage 4 | The large-document queue and its summaries |

`python cli.py health` lists every table with its row and file counts.

## Per-tenant directory (optional, #671)

Off by default (`delta_lake.tenant_path_prefix: false` in `config.yml`). `DELTA_TENANT_PATH_PREFIX` overrides the flag when it is set to `true`, `false`, `1`, `0`, `yes`, `no`, `on`, or `off`. Any other value raises at startup. Unset means the config flag.

When the flag is on, `LakehouseManager` and `DeltaHelper` append one segment to a root they resolved themselves (`DELTA_LAKE_PATH`, else `delta_lake.base_path`): `{root}/{tenant_id}/{table}`. Spill (`_write_spill`), trash (`_trash`), audit (`_audit`), and `<table>_history` follow that directory. The segment is `SITE_ID`, else `TENANT_ID`, else `REDIS_SITE_ID`, else `default` — the same id as Redis seen-sets. It must be one path segment matching `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. A slash, `..`, or other shape raises, and no directory is created.

An explicit `base_path` (a test, a tool, or `DeltaHelper` handing its path to the manager) is not prefixed again. Pass `{root}/{tenant_id}` yourself if that caller should see the prefixed lake. `kafka-delta-ingest` takes a table path on its command line and does not read this flag.

The flag does not move files and does not resize a PVC. Claim sizes stay 500Gi (`deltaLake.persistence.size`) and 200Gi (`scrapyApp.persistence.deltaLake.size`). How to size a shared claim for more than one tenant is in [REDIS_CAPACITY.md](../REDIS_CAPACITY.md). The decision is [ADR-0004](../../../docs/adr/0004-per-tenant-delta-prefix.md).

### Upgrade

Enabling the flag on a root that already has tables makes writers create empty tables under the prefix and leave the old ones unread.

1. Stop Python writers and `kafka-delta-ingest`.
2. Resolve the id above. A single-site install with `SITE_ID`, `TENANT_ID`, and `REDIS_SITE_ID` all unset uses `default`.
3. Create `{root}/{id}` and move each table directory plus `_write_spill`, `_trash`, and `_audit` into it. Do not copy. A copy doubles the bytes on the PVC until the old tree is deleted, and the old tree is not read.
4. Point every `kafka-delta-ingest` table argument at `{root}/{id}/{table}`.
5. Set the flag, or `DELTA_TENANT_PATH_PREFIX=true`, and start writers.
6. To go back, stop writers, move those directories up to `{root}`, and set the flag false before any new commits exist only under the prefix.

## Query from Python

### Through the pipeline's `LakehouseManager`

```python
from src.lakehouse.lakehouse_manager import LakehouseManager

lake = LakehouseManager(start_workers=False)   # read-only use; no background writer thread
rows = lake.read("stage2_page_analysis",
                 filters=[("domain", "=", "uconn.edu")],      # partition pruning
                 columns=["url", "word_count"])
print(len(rows), lake.count("stage1_discovery"))
old = lake.read("stage2_queue", version=0)                    # time travel
```

`read()` returns a list of dicts. Filters use pyarrow DNF tuples (`[("col", "op", value), ...]`).

### With the `deltalake` package directly (pandas / pyarrow)

```python
from deltalake import DeltaTable

dt = DeltaTable("data/delta_lake/stage2_page_analysis")
df = dt.to_pandas(columns=["url", "word_count"], filters=[("domain", "=", "uconn.edu")])
print(df.sort_values("word_count", ascending=False).head())
print(dt.version(), dt.history()[:3])          # current version and recent commits
```

### From Spark or Scala (outside this repo)

Any Delta-capable engine can read the same directories. For example, with `delta-spark`:

```scala
val df = spark.read.format("delta").load("/path/to/data/delta_lake/stage2_page_analysis")
df.groupBy("domain").count().show()
```

Write to the lake only through the pipeline (`LakehouseManager.write()` / `merge_into()`). Writing from a separate engine while the workers are running risks commit conflicts on the queue tables.

## Convert / export (CSV, JSON lines, Parquet)

```bash
python cli.py export --table stage2_page_analysis --format parquet --output exports/
python cli.py export --format csv --output exports/          # every table
```

From Python, exports are streamed in batches, so memory use stays bounded however large the table is:

```python
result = lake.export("stage1_discovery", "exports/discovery.csv", format="csv",
                     filters=[("domain", "=", "uconn.edu")], max_rows_per_file=1_000_000)
print(result)   # {"rows": ..., "files": [...], "size_mb": ...}
```

`export.batch_size`, `export.max_rows_per_file` and `export.max_bytes_per_file` in `config.yml` set the defaults. If you export a table to Parquet and later want it back as a Delta table, use `deltalake.write_deltalake(path, pandas_or_arrow_table)`.

URL scores (not Delta rows) have a separate offline snapshot, `data/exports/scoring_YYYYMMDD.parquet`. It does not read the lake. Schema and compaction: [scoring snapshots](../../../docs/scoring_snapshot.md).

```bash
python cli.py scoring-export --urls urls.txt
```

## Connecting the Scrapy pipeline to the lake

Spiders and workers don't open tables themselves. They call `src.utils.delta.get_delta()` / `LakehouseManager.get_instance()`, which resolve the lake root as described above. To point a whole run at another lake, set `DELTA_LAKE_PATH` for every process (spiders, workers, exporter). Otherwise the processes write to different directories.
