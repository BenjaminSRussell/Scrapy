# Stage 3 online-learning feedback for URL scoring

- **Status:** Draft. Design only. No table, migration, model, training job, or config key is added here.
- **Date:** 2026-10-14
- **Issue:** [#751](https://github.com/BenjaminSRussell/Scrapy/issues/751)

This file does not change runtime behavior. [`URLValueAssessor`](../../Scraping_project/src/common/url_value_assessor.py) does not learn from Stage 3, online or otherwise. Nothing here writes a feedback row, fits a model, or adjusts a score.

[#751](https://github.com/BenjaminSRussell/Scrapy/issues/751) asks for a design: a feedback table whose Stage 3 quality label can later feed URL-scoring weights, and a batch retrain stub. The stub is off by default. CI must not run ML training. This document is that design. It is not the implementation.

"Online" is the issue's name for the loop from crawl outcomes back to scoring. The loop in this design is a batch description that does not run. `assess_url` stays a function of its arguments and the class constants. It does not update a weight per URL.

## What exists today

Scoring is static. `URLValueAssessor.assess_url` starts at 50 and adds fixed integers for path patterns, document extensions, path length, query shape, JS hints, crawl depth, and domain suffix. Those integers live on the class (`HIGH_VALUE_PATTERNS`, `LOW_VALUE_PATTERNS`, `DOCUMENT_EXTENSIONS`, and the methods that apply them). [#267](https://github.com/BenjaminSRussell/Scrapy/issues/267) locks that behavior with deterministic unit tests in [`test_url_value_and_scoring.py`](../../Scraping_project/tests/unit/common/test_url_value_and_scoring.py). Those tests are not a training set. This design does not change them.

Optional history is not this loop. When `use_historical_data` is left on, the assessor may add a path boost or replace the domain term using [`CrawlDataManager`](../../Scraping_project/src/common/crawl_data_manager.py). That manager reads `stage1_discovery` and `stage1_errors`. Its `avg_content_quality` is a formula over Stage 1 success rate and average depth. `is_valuable_path` is the most common Stage 1 path prefixes. Neither reads `stage3_summaries`, `quality_score`, or `is_low_quality`. Do not describe that cache as online learning or as a Stage 3 label.

Stage 3 does not emit a quality label. [`Stage3Worker`](../../Scraping_project/src/stage3/stage3_worker.py) reads `stage2_page_analysis` and appends summaries to `stage3_summaries` (`TABLE_STAGE3_SUMMARIES` in [`constants.py`](../../Scraping_project/src/core/constants.py)). The PyArrow shape is `STAGE3_SUMMARY_SCHEMA` in [`schemas.py`](../../Scraping_project/src/core/schemas.py): `url`, `url_hash`, `summary`, `word_count`, `keywords`, `quality_score`, `timestamp`. `quality_score` on that row is copied from the Stage 2 analysis row (`doc.get("quality_score", 0)`). Stage 2 computes it as a heuristic of word count and text-to-HTML ratio in [`stage2_worker.py`](../../Scraping_project/src/stage2/stage2_worker.py) and [`intelligent_analyzer.py`](../../Scraping_project/src/stage2/intelligent_analyzer.py): word contribution capped at 0.6, ratio contribution capped at 0.4, so the stored score is about 0 to 1. It is not a class label and not a model output.

The worker treats a document as eligible when `is_low_quality` is false (a missing key counts as true), `is_massive_doc` is false, `has_error` is false, and `text_content` is present. MinHash can still drop an eligible document as a near-duplicate. A failed summary writes no row. None of those outcomes is stored as a label for the assessor.

The [stage map](../STAGE_MAP.md) still records the [#612](https://github.com/BenjaminSRussell/Scrapy/issues/612) naming split. Stage 3's write constant is `stage3_summaries`. Older lakes also hold Stage 3 rows under `stage4_summaries` (`LEGACY_TABLE_STAGE3_SUMMARIES`). The worker reads both so an old lake is not summarized twice, and it writes only `stage3_summaries`. This design uses the write name. It does not relabel the legacy table, and it does not edit the stage map.

There is no `url_scoring_feedback` table, in Delta or in Postgres. [`schemas.py`](../../Scraping_project/src/core/schemas.py) does not list it. No migration is added here.

Where tables live today, and where this sketch does not add one, is the [data usage guide](../../Scraping_project/docs/guides/DATA_USAGE.md).

## Feedback table

Proposed name: `url_scoring_feedback`. The block below is a column sketch. It is not DDL, not a Delta schema, not a SQL migration, and not an entry in `schemas.py`. No job creates it.

One row is one static weight that fired for one URL, plus the Stage 3 outcome for that URL. Weights that did not fire are not rows. The assessor does not read the table.

```text
# Column sketch. Not a migration. Not loaded. Not created.

url_scoring_feedback
  feedback_id             string     proposed row id; no table owns it today
  crawl_job_id            string     scrapy.crawl_job_id when the span has one; else empty
  url                     string     URL passed to assess_url
  url_hash                string     hash Stage 2 and Stage 3 already store
  domain                  string     registrable domain the assessor extracts
  weight_name             string     one static boost name from the weight table
  static_delta            int        integer that boost adds today; not a learned weight
  assessor_value_score    int        URLValue.value_score after the 0-100 clamp
  stage3_quality_label    string     accept, reject_low_quality, or hold
  stage2_quality_score    float      Stage 2 heuristic when the analysis row had one; else empty
  stage3_table            string     stage3_summaries only when a summary was written; else empty
  proposed_weight_delta   int        empty until the batch stub (which is off) fills it
  applied                 bool       false; nothing applies a delta
  labeled_at              timestamp  when a future writer would record the row; no writer exists
```

| Column | Sketch type | Meaning |
|---|---|---|
| `feedback_id` | string | Proposed row id. Not a key in any table today. |
| `crawl_job_id` | string | `scrapy.crawl_job_id` when that span attribute is set. Empty is allowed. This is not a new id space. |
| `url` | string | The URL that was scored. |
| `url_hash` | string | The hash Stage 2 and Stage 3 already store. Join key to `stage2_page_analysis` and `stage3_summaries`. This design does not define a new hash. |
| `domain` | string | Registrable domain `URLValueAssessor._extract_domain` returns. |
| `weight_name` | string | One name from the weight table below. One row per `(url_hash, weight_name)` that fired. |
| `static_delta` | int | The integer that boost added in the static assessor. A snapshot, not a parameter to train. |
| `assessor_value_score` | int | `URLValue.value_score` after clamping to 0-100. |
| `stage3_quality_label` | string | `accept`, `reject_low_quality`, or `hold`. Defined below. Not a column on `stage3_summaries`. |
| `stage2_quality_score` | float | Stage 2's heuristic (about 0 to 1) when that analysis field was present. Empty when it was not. Not a model output. On `accept`, the summary row carries the same number Stage 3 copied, including the worker's default of 0 when the analysis row omitted it. |
| `stage3_table` | string | `stage3_summaries` when Stage 3 appended a summary for this `url_hash`. Otherwise empty. Never `stage4_summaries`. |
| `proposed_weight_delta` | int | Empty unless the batch stub later fills it. The assessor does not read it. |
| `applied` | bool | `false`. No component applies a delta to the assessor. |
| `labeled_at` | timestamp | When a future writer would record the row. No writer is added here. |

### Stage 3 quality label

The label is an outcome class this design defines. Stage 3 does not write it.

| Label | When it would be set | May move a weight later |
|---|---|---|
| `accept` | Stage 3 appended a summary for this `url_hash` to `stage3_summaries`. | yes |
| `reject_low_quality` | The analysis row was excluded because `is_low_quality` was true, including when the key was missing (the worker's default). | yes |
| `hold` | Massive doc, `has_error`, empty `text_content`, MinHash near-duplicate, already summarized, or summarization returned no row. | no |

`hold` is recorded so the URL is not mistaken for unlabeled. It is not a quality judgment about the URL pattern. Massive docs belong to Stage 4. Duplicates are similarity. Errors are failures. Those rows keep `proposed_weight_delta` empty.

Only `accept` and `reject_low_quality` are allowed to feed a weight. The feed is the batch stub below. That stub is off and does not run. There is no per-request update inside `assess_url`.

### Weights the label would point at

These are the static boosts in `assess_url` today. The table names them so a feedback row can point at one. It does not change them. A row exists only for a boost that actually fired (non-zero delta). `weight_name` values are names this sketch assigns. They match an existing `reasons` string where the assessor appends one.

| `weight_name` | What adds it today | `static_delta` |
|---|---|---|
| `document_file` | Path extension in `DOCUMENT_EXTENSIONS`. The query string is ignored. The reason string is `document_file_{ext}`. | pdf +40; doc/docx +35; ppt/pptx +30; xls/xlsx +25 |
| `historical_valuable_path` | `CrawlDataManager.is_valuable_path` when history is on. Stage 1 path frequency, not a Stage 3 label. Reason string matches this name. | +35 |
| `high_value_pattern` | `HIGH_VALUE_PATTERNS` on the path. Reason string matches this name. | +30 |
| `low_value_pattern` | `LOW_VALUE_PATTERNS` on the path. Reason string matches this name. | -40 |
| `short_path` | Two path segments or fewer. Reason string matches this name. | +10 |
| `deep_path` | More than five path segments. Reason string matches this name. | -10 |
| `dynamic_params` | Query contains `id=`, `page=`, `category=`, or `search=`. Reason string matches this name. | +5 |
| `other_query` | Any other non-empty query. The assessor applies -5 and appends no reason. This name exists only in the sketch. | -5 |
| `js_heavy` | `_assess_js_requirement`, in order: JS confidence above 0.7, else a `JS_PATTERNS` match, else `/#/` or `/app/` in the URL. Reason `js_heavy` when the boost is non-zero. | +20, else +15, else +25 |
| `depth_penalty` | `depth` greater than 3. The reason string today is `depth_{depth}`, not this sketch name. | -5 times `(depth - 3)` |
| `valuable_domain` | Non-zero `get_domain_value_boost` replaces the suffix rules: quality at least 80 is +30, at least 60 is +20, at least 40 is +10, below 20 is -20. A boost of 0 (quality in `[20, 40)`) does not replace them. Otherwise the last host label `.edu` or `.gov` is +10, and a raw host with `len(labels) - 2 > 2` is -5. Those two suffix terms can both apply. The penalty uses the raw host, not the public-suffix domain. The reason `valuable_domain` is appended only when the domain term is positive. | +30, +20, +10, or -20 from history, or +10 and/or -5 from the suffix rules |

The base of 50 is not a weight and is not a row. Clamping the total into 0-100 is not a weight. `content_likelihood` and `recommended_spider` are not weights. This design does not retune 50, 70, or 40.

`historical_valuable_path` and the historical branch of `valuable_domain` stay in the name list because they are integers the assessor already adds. A Stage 3 label must not be written back into `CrawlDataManager`. The batch stub must not treat Stage 1 success rate as `stage3_quality_label`.

## Batch retrain stub

The batch retrain is this description. It is not code. It is off by default.

Off means no switch exists. This change adds no config key. [ADR-0001](../adr/0001-config-yml-is-canonical.md) still says `Scraping_project/config.yml` is canonical; this draft adds no key there. It also adds no environment variable, CLI flag, cron, compose service, Helm value, Makefile target, or workflow step. Nothing calls the stub. Reading this file does not run it.

It is not a trainer. It does not fit a model, download weights, import an ML library, or update `URLValueAssessor`. It does not write a migration.

If a later change turns it on, outside this issue and outside CI, the intended behavior is only:

1. Read feedback rows where `applied` is false and `stage3_quality_label` is `accept` or `reject_low_quality`.
2. Group those rows by `weight_name`.
3. Store one proposed integer on `proposed_weight_delta` for that weight name.

The count rule and any cap on that integer are not chosen here. `hold` rows stay empty. `applied` stays false. The class constants on the assessor stay the source of the live score. Copying a delta into the assessor is a different change. This stub does not do it.

```text
assess_url (static; unchanged)
        |
        v
Stage 2 analysis -> Stage 3 summary or skip
        |
        v
url_scoring_feedback   (sketch only; not written)
        |
        v
batch retrain          (this section; off; no code)
        |
        v
proposed_weight_delta, applied = false
        |
        x
URLValueAssessor does not read the row
```

## CI

CI must not run ML training. This change does not add a dependency, a Makefile target, or a workflow step.

[`.github/workflows/main.yml`](../../.github/workflows/main.yml) runs the default pytest set. It does not train, and this design does not add a job that does. Compose config checks are not a training step either. Required checks stay free of model fitting, weight downloads, and this batch stub.

A later implementation must keep the stub out of the default pytest run and out of required workflows. A test, if one is ever added, may only assert that no trainer is wired in. This change adds no test.

## Out of scope

- No edit to `url_value_assessor.py` or any other runtime file. The assessor does not learn online. This design does not say that it does.
- No SQL migration, no Delta schema registration, no model artifact, no training script.
- No new package in a requirements file, `pyproject.toml`, or the `ml` extra. The Stage 3 worker's summary is extractive string slicing, not that extra.
- No edit to `config.yml`, `Cargo.lock`, Helm, the dashboard, docker-compose, or `README`.
