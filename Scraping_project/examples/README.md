# Examples

Stage 4 entity-summarization demos. They are **not** part of the pipeline or of CI.
Run them from `Scraping_project/` with the project venv active (see
[CONTRIBUTING.md](../../CONTRIBUTING.md)).

## Extra dependencies

Both examples need the Stage 4 NLP stack, which is **not** in `requirements.txt`:

```bash
# from Scraping_project/
pip install -r requirements-stage4.txt     # sentence-transformers, transformers, torch (~2 GB)
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
