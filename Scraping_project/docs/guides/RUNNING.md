# Running guide

## Prerequisites

- Python 3.11+ and a virtualenv with `pip install -r requirements.txt` (add `dev-requirements.txt` for tests and linters).
- Docker with Compose, for Redis, Postgres, Prometheus, Grafana and the stage workers.
- Optional: run `playwright install chromium` for the JavaScript spider, and a GPU (set `device: cuda`) for summarization.

Run every command from `Scraping_project/`.

## Start everything (Docker Compose)

```bash
cd Scraping_project
python start.py                  # docker-compose up -d for every service, then waits for them
python start.py --reset-delta    # also reset the Delta tables and reload seeds from the seed CSV
```

The Compose services are `scraper`, `stage1-worker` … `stage4-worker`, `redis`, `postgres`, `redis-exporter`, `postgres-exporter`, `prometheus` and `grafana`. To start only some of them, use `docker-compose up -d <service>`. To stop everything, run `python shutdown.py`. On SIGTERM, `LakehouseManager` drains its Delta write queue before the process exits. For Kubernetes (`python start.py --env k8s --stage …`), see [DEPLOYMENT.md](../../DEPLOYMENT.md).

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

## Seeds

```bash
python cli.py seeds list --active-only
python cli.py seeds add https://www.example.org/ --note "new site" --actor me
python cli.py seeds disable https://old.example.org/ --actor me
python reseed.py --csv data/raw/uconn_urls.csv    # bulk-load a CSV into seed_urls
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

Then add seeds with `cli.py seeds add` or `reseed.py --csv`. Every Stage 1 spider applies the same precedence: `-a allowed_domains` first, then `stage1.allowed_domains`, then `uconn.edu`. Without `-a start_urls`, the spider starts from the active rows in `seed_urls`.

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
