# Data usage guide (Delta Lake)

All pipeline data lives in Delta Lake tables, which are Parquet files plus a `_delta_log/` transaction log. The pipeline no longer uses SQLite.

## Where the data is

The lake root is `DELTA_LAKE_PATH` if that is set, otherwise `delta_lake.base_path` (default `./data/delta_lake`). Compose sets it to `/data/delta` inside the containers, which is `./data/delta` on the host. Each table is a directory under the root:

| Table | Written by | Contents |
|---|---|---|
| `seed_urls` | `cli.py seeds`, `reseed.py`, scout seed expansion | Seeds and their active flag |
| `uconn_urls` (= `stage1.domain_urls_table`) | Stage 1 | URLs on the allowed domains |
| `stage1_discovery` | Stage 1 | Every discovered URL. Partitioned by `domain` |
| `stage1_errors`, `stage1_offsite_candidates`, `js_spider_queue` | Stage 1 | Fetch errors, off-site links, JS render candidates |
| `stage2_queue` | Stage 1 → 2 | Work queue (`status`: `pending` / `completed` / `failed`) |
| `stage2_page_analysis` | Stage 2 | Page analysis (text stats, quality, links). Partitioned by `domain` |
| `stage2_errors` | Stage 2 | Analysis failures |
| `stage3_analytics`, `stage3_summaries` | Stage 3 | Summaries and analytics for normal pages |
| `stage4_large_docs`, `stage4_large_doc_summaries`, `stage4_summaries` | Stage 4 | The large-document queue and its summaries |

`python cli.py health` lists every table with its row and file counts.

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

## Connecting the Scrapy pipeline to the lake

Spiders and workers don't open tables themselves. They call `src.utils.delta.get_delta()` / `LakehouseManager.get_instance()`, which resolve the lake root as described above. To point a whole run at another lake, set `DELTA_LAKE_PATH` for every process (spiders, workers, exporter). Otherwise the processes write to different directories.
