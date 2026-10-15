# Examples

Optional scripts. They are **not** part of the pipeline image, of CI, or of
Docker Compose. Run them from `Scraping_project/` with the project venv active
(see [CONTRIBUTING.md](../../CONTRIBUTING.md)).

## Stage 4 entity summarization

These two demos are **not** part of the pipeline or of CI.

## Extra dependencies

Both Stage 4 demos need the Stage 4 NLP stack. That install is **optional** and
**heavy** (~2 GB of sentence-transformers, transformers, and torch) and is
**not** in `requirements.txt` or the CI install:

```bash
# from Scraping_project/
make install-stage4
# or: pip install -r requirements-stage4.txt
```

The first run downloads two Hugging Face models (`sentence-transformers/all-MiniLM-L6-v2`
and `facebook/bart-large-cnn`, about 1.7 GB) into `~/.cache/huggingface`. After that
they work offline with `HF_HUB_OFFLINE=1`. Both run on CPU (`device=-1`). Expect a few
minutes for the first summarization.

## `entity_summarization_demo.py`: self-contained walkthrough

```bash
# from Scraping_project/
python examples/entity_summarization_demo.py
```

| | |
|---|---|
| Input | built-in sample documents (no Redis, Kafka, or crawl needed) |
| Shows | fact aggregation and semantic dedup, chronological sorting, BART summarization, then the full `Stage4EntityWorker` |
| Writes | the `entity_summaries` Delta table under `DELTA_LAKE_PATH`, else `delta_lake.base_path` in `config.yml`, else `./data/delta_lake`. Point `DELTA_LAKE_PATH` at a scratch directory (`DELTA_LAKE_PATH=/tmp/demo_lake python examples/...`) to keep the demo away from a real lake |

## `stage4/entity_worker_example.py`: worker runner against real data

```bash
# from Scraping_project/ (this script has no sys.path shim, so set PYTHONPATH)
PYTHONPATH=. python examples/stage4/entity_worker_example.py --mode delta --limit 50
PYTHONPATH=. python examples/stage4/entity_worker_example.py --mode delta --input-table stage3_analytics
PYTHONPATH=. python examples/stage4/entity_worker_example.py --mode kafka --kafka-topic final_categorized
```

| Mode | Needs |
|------|-------|
| `delta` (default) | an existing Delta lake with the input table (default `stage3_analytics`) produced by a Stage 3 run; same `DELTA_LAKE_PATH` lookup as above |
| `kafka` | a reachable Kafka broker (`KAFKA_BOOTSTRAP_SERVERS`) and the input topic (default `final_categorized`); runs until interrupted |

Optional model settings are in `config/entity_summarization.example.yml`.

## `airflow/scraping_pipeline_dag.py`: optional Airflow schedule

An example DAG that runs the existing CLI stages (and one orchestrator call
for stage 4) from Airflow connections. `airflow/stage_env.py` maps those
connections onto `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` and `REDIS_DB`
(never `REDIS_URL`), plus the optional Postgres and Kafka variables.

It is **not** a Compose service, **not** in `requirements.txt`, and **not**
started by `python start.py`. The core profile is unchanged. Airflow itself
is installed only on the scheduler you already run.

Variables, connection ids, and the task commands are documented in
[airflow/README.md](airflow/README.md). Copy `airflow/scraping_pipeline_dag.py`
and `airflow/stage_env.py` together onto the Airflow dags path. The DAG is
not on a schedule until you trigger it.
