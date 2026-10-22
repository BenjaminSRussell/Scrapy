# Running guide

## Prerequisites

- Python 3.11+ and a virtualenv with `pip install -r requirements.txt` (add `dev-requirements.txt` for tests and linters).
- Docker with Compose, for Redis, Postgres, Prometheus, Grafana and the stage workers.
- Optional: run `playwright install chromium` for the JavaScript spider, and a GPU (set `device: cuda`) for summarization.

Run every command from `Scraping_project/`.

## Start everything (Docker Compose)

```bash
cd Scraping_project
python start.py                  # core + monitoring profiles, then waits for Postgres
python start.py --reset-delta    # also reset the Delta tables and reload seeds from the seed CSV
```

Compose profiles (#145) are `core` (redis, scraper, stage 1–3 workers, Postgres), `monitoring` (Prometheus, Grafana, exporters, ops dashboard on port 8080), `streaming` (Kafka; Postgres is also on `core`) and `observability` (Loki / Jaeger / OTEL, overlay file). `stage4-worker` stays on profiles `ml` and `full` (ML/OCR image, #144), not `core`. `python start.py` enables `core` and `monitoring` only, so it does not start Stage 4, Kafka or the OTEL overlay. Start Stage 4 with `docker compose --profile ml up -d stage4-worker`. The same command shape with `--profile streaming` or `--profile observability` (and `-f docker-compose.observability.yml` for the overlay) opts into the rest. That overlay file also starts the unprofiled Prometheus/Grafana pack (`127.0.0.1:9095` and `:3001`); `docker compose -f docker-compose.observability.yml up -d` starts the pack without the app. See [COMPOSE.md](../../COMPOSE.md). To stop everything, run `python shutdown.py`. On SIGTERM, `LakehouseManager` drains its Delta write queue before the process exits. For Kubernetes (`python start.py --env k8s --stage …`), see [DEPLOYMENT.md](../../DEPLOYMENT.md).

## Run the stages yourself (no Compose)

```bash
scrapy list                                   # base, deep_dive, depth, javascript, scout
scrapy crawl scout                            # Stage 1: discovery → stage1_discovery + stage2_queue
python -m src.workers.stage2_worker           # Stage 2: analyse pending stage2_queue rows
python -m src.workers.stage3_worker           # Stage 3: summarize
python -m src.workers.stage4_worker           # Stage 4: large documents (PDFs, pages over stage2.massive_doc_threshold)
python cli.py pipeline --skip-stage1          # or run stages 2-3 once, in order
python cli.py health                          # row and file counts for every Delta table
```

### Exit codes for `cli.py scrapy` and `cli.py deep_dive`

| Exit | Meaning |
|------|---------|
| `0` | Every spider finished, or stopped on a `closespider_*` limit. |
| `1` | A spider raised, logged an `ERROR` line, or ended on any other finish reason. An interrupted run (SIGTERM/SIGINT) also exits `1`. |
| `2` | An unknown spider name (the available spiders are printed to stderr), or an experimental spider without `--experimental`. |

CI, cron and Kubernetes Jobs can rely on these codes to tell a failed crawl from a good one (#1559).

## Seeds

```bash
python cli.py seeds list --active-only
python cli.py seeds add https://www.example.org/ --note "new site" --actor me
python cli.py seeds disable https://old.example.org/ --actor me
python scripts/reseed.py --csv data/raw/uconn_urls.csv    # bulk-load a CSV into seed_urls (`python reseed.py` is deprecated)
```

## Crawling a different domain

The bundled config targets `uconn.edu`. There are two ways to point it elsewhere.

**For a single run, use spider arguments.** Both values may be comma-separated:

```bash
scrapy crawl scout \
  -a allowed_domains=example.org,docs.example.org \
  -a start_urls=https://www.example.org/
```

**To change it permanently, edit `config.yml`:**

```yaml
stage1:
  allowed_domains: [example.org]
  domain_urls_table: example_urls   # or write_domain_urls: false
```

Then add seeds with `cli.py seeds add` or `scripts/reseed.py --csv` (`python reseed.py` is deprecated). Every Stage 1 spider applies the same precedence: `-a allowed_domains` first, then `stage1.allowed_domains`, then `uconn.edu`. Without `-a start_urls`, the spider starts from the active rows in `seed_urls`.

## Resuming after a crash

Every stage writes its progress to Delta tables or Redis, so to resume you usually just start the same command again.

- **Stage 1.** Visited URLs are recorded in Redis seen-sets, so a restarted crawl skips pages it already fetched. To also keep Scrapy's in-flight scheduler queue across restarts, give the run a job directory and reuse it:

  ```bash
  scrapy crawl scout -s JOBDIR=data/jobs/scout-1     # Ctrl-C once for a clean pause
  scrapy crawl scout -s JOBDIR=data/jobs/scout-1     # resumes the same queue
  ```

- **Stages 2–4.** The workers only take rows whose `status` is `pending`, and mark them `completed` or `failed` once processed. Rows that were in flight when a worker died are still `pending`, so the next run picks them up again. To see what's left, run `python cli.py validate` and `python cli.py queue-gc --dry-run`.
- **The Delta write queue.** A clean shutdown (SIGTERM, `shutdown.py`) drains the in-memory write queue and spills anything left to `_write_spill/`. A hard kill (SIGKILL/OOM) loses only the batches that were still in memory; the `DeltaWriteQueueBacklog` alert warns when that window grows. Use `LakehouseManager.replay_spilled_writes()` to load spilled batches back.
- **Emergency stop.** `python cli.py killswitch on --reason "..." --actor me` stops Stage 1 and 2 downloads cluster-wide. `killswitch off` releases it.
