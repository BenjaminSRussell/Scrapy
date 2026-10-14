# URL-scoring Parquet snapshots

Offline columnar dump of URL scores for a data visualizer or eval notebook (#1103). This is not the Prometheus exporter (`Scraping_project/scripts/export_metrics_multiformat.py`) and it is not the Stage 3 feedback design in [product/751-online-learning.md](product/751-online-learning.md). Nothing here trains a model, edits assessor weights, crawls, or reads Redis, Kafka, or the Delta lake.

Run it from `Scraping_project/`:

```bash
python scripts/export_scoring_snapshot.py --urls urls.txt
python cli.py scoring-export --urls urls.txt --jsonl scored.jsonl
```

Both commands call the same writer. The default directory is `data/exports/` (gitignored). The file for a UTC day is `scoring_YYYYMMDD.parquet`. Pass `--day 20261014` to choose the filename. That date is not each row's `ts`.

## Schema

One snappy Parquet file. Five columns, in this order. `features` is a single UTF-8 JSON object so new keys can appear without a new physical column.

| Column | Parquet type | Null | Meaning |
|---|---|---|---|
| `url_hash` | string | no | First 16 hex chars of SHA-256 over the canonical URL. Same value as `src.utils.url_canon.url_hash` (the Stage 1 / Stage 2 / Stage 3 hash). |
| `score` | double | no | `URLValueAssessor.value_score` in `[0, 100]` when the row has a `url` and no `score`. A JSONL row that sets `score` stores that finite number instead (it is not clamped). |
| `features` | string | no | JSON object. Keys are sorted. `NaN` is rejected. |
| `stage` | string | no | Token, default `stage1`. Pattern `[A-Za-z0-9_.:-]{1,64}`. |
| `ts` | timestamp[us, UTC] | no | When the score was recorded. A row may set `ts`; otherwise the export's clock is used. Naive timestamps are UTC. |

`url_hash` is computed from the canonical URL, so `https://WWW.Example.edu/About/` and `https://www.example.edu/about` share a row. A supplied `url_hash` that disagrees with that canonical hash is skipped. A row with only `url_hash` and `score` (no `url`) is kept and is not re-scored.

Assessed `features` (static assessor, `use_historical_data=False`):

| Key | Meaning |
|---|---|
| `url` | URL that was scored |
| `value_score` | Same integer the `score` column stores for an assessed row |
| `content_likelihood` | `high`, `medium`, or `low` |
| `recommended_spider` | `scout`, `depth`, or `js` |
| `reasons` | Assessor reason strings, in assessor order |
| `metadata` | Assessor metadata object (booleans such as `is_document`) |
| `depth` | Depth passed in, clamped to an integer `>= 0` |
| `js_confidence` | Clamped to `[0, 1]` |
| `publication_date` | Present only when the input set it |
| `decay_score` | `calculate_decay_score(publication_date, reference_date=ts)` from `scoring_metrics.py`. Not the `score` column. Omitted when there is no publication date. A future publication date skips the row. |

JSONL `features` (object or JSON string) replace assessed keys with the same name. They do not change the `score` column unless the row itself sets `score`.

The same `(url_hash, stage)` in one file keeps the row with the latest `ts`. Equal timestamps keep the later input. A second run on the same day merges into that file. `--no-merge` replaces it.

Empty input (a file of only comments and blank lines) still writes a readable zero-row file with this schema. If every non-empty input row fails, the command writes nothing, does not compact, and exits 1.

Snapshots are not written under `data/delta_lake`, `data/dlq`, or `data/kafka_spill`.

## Compaction

Daily names only: `scoring_YYYYMMDD.parquet` in the output directory. Files whose date is strictly older than `--keep-days` (default 14) UTC days before the snapshot day are read, deduped with `scoring_archive.parquet` on `(url_hash, stage)` by latest `ts`, written back to that archive with the schema above, and then deleted.

Left untouched: the snapshot day's file, the archive itself, symlinks, unreadable names such as `scoring_20261340.parquet`, and any other file (`notes.parquet`, Prometheus exports, profiles). If a candidate file is not this schema, compaction stops and deletes nothing. `--no-compact` skips the step. `--keep-days 0` archives every daily file except the snapshot day.

## Optional cron

Not installed. Not a Compose service, Helm value, or workflow. Adjust the paths. The process only reads the URL file and writes Parquet.

```cron
15 2 * * * cd /opt/scraping/Scraping_project && /opt/scraping/.venv/bin/python scripts/export_scoring_snapshot.py --urls /opt/scraping/Scraping_project/data/seeds/urls.txt --output-dir /opt/scraping/Scraping_project/data/exports --keep-days 14
```
