# Ops assistant: "why is stage2 lagging?"

- **Status:** Design only. No runner, model, dashboard, alert, or metric is added here.
- **Date:** 2026-10-14
- **Issue:** [#780](https://github.com/BenjaminSRussell/Scrapy/issues/780)

Issue #780 named `docs/product/ops-assistant.md`. This file is that design. The numbered path keeps it from colliding with other product docs.

The question is a Control Center stretch: an operator asks why Stage 2 is lagging and gets a metrics summary. [ROADMAP](../ROADMAP.md) keeps Control Center (ops) and Grafana (history, PromQL, alerts) separate. This design does not merge them and does not change the dashboard.

## What ships today

Stage 2 work waits on the Delta table `stage2_queue`, not on Redis. The gauge `stage2_queue_pending` is the pending row count at the start of the last Stage 2 run ([`stage2_worker.py`](../../Scraping_project/src/stage2/stage2_worker.py)). `redis_queue_length` is the JS render priority queue. It is not Stage 2 depth, and 10000 is not a limit on `stage2_queue` ([Redis capacity](../../Scraping_project/docs/REDIS_CAPACITY.md)).

Control Center's `/api/metrics` reads a Prometheus exposition scrape (`METRICS_UPSTREAM`, default `http://127.0.0.1:9090/metrics`). It does not evaluate PromQL ([dashboard README](../../Scraping_project/dashboard/README.md)). These templates are for Prometheus `GET /api/v1/query`. Nothing in this change adds that call.

Published objectives are only the three in [Pipeline SLOs](../../Scraping_project/docs/guides/PIPELINE_SLOS.md):

| SLO | Objective | Shipped comparison |
|---|---|---|
| Stage 2 success | at least 99% over 24h | `stage2:success_ratio:24h < 0.99` while the 24h window has rows (`Stage2SuccessSLO`) |
| QueueBacklog | `redis_queue_length` stays at or below 10000 | `max by (queue) (redis_queue_length) > 10000` |
| Kafka consumer lag | max lag stays at or below 250000 messages | `max_over_time(kafka_consumergroup_lag_max[5m]) > 250000` (`KafkaConsumerLagSLO`) |

No fourth SLO. No per-site URL cap, no member-count cap, and no numeric cap on `stage2_queue_pending`. Warning alerts that are not in the table above (`KafkaHighConsumerLag`, `KafkaVeryHighConsumerLag`, `Stage2OversizedRejectsHigh`, and the rest) are not objectives. Do not retune the three lines to make an answer look healthy.

Metric names below are the series on main when this doc was written. This design does not add or rename series. If a monitoring change renames one, update the template in a docs-only follow-up. Do not guess a replacement name. An empty result is "no series", not a zero.

## Architecture

Prompt plus a closed tool list. No production LLM.

```
question "why is stage2 lagging?"
        |
        v
playbook (fixed tool order; no free-form PromQL)
        |
        v
tool runner: each template sent verbatim to Prometheus /api/v1/query
        |
        v
summary, filled only from tool results, using the prompt below
```

A person can run the playbook in Grafana today. A later Control Center box would call the same templates and show the same summary. A model is not on this path, not configured, and not required. If one is added later it may only select tools from this list and phrase the summary. It does not get a shell, a Prometheus admin token, or the ability to write PromQL.

The playbook for this question calls every tool in the list, in order. It does not take a parameter from the question text. That keeps a label such as `url` out of the query.

## Prompt

The prompt is the contract for the summary. It is not a deployed system message.

```
You explain Stage 2 lag for one operator. You do not change the crawl.

Call only the tools in docs/product/780-ops-assistant.md, in that order.
Send each PromQL template verbatim. Do not write, edit, or combine PromQL.
Do not query Postgres, Delta, Redis keys, or logs.

Use only these objectives:
- Stage 2 success is at least 99% over 24h (stage2:success_ratio:24h).
- QueueBacklog is redis_queue_length above 10000.
- Kafka consumer lag is max_over_time(kafka_consumergroup_lag_max[5m]) above 250000 messages.
Do not add an objective. Do not cite 10000 or 100000 Kafka warnings as SLOs.
Do not invent a site cap or a cap on stage2_queue_pending.
Do not tell the operator to raise 99%, 10000, or 250000.

redis_queue_length is the JS priority queue, not stage2_queue.
stage2_queue_pending is the pending Delta rows at the last Stage 2 run, not a live depth and not an SLO.
If stage2:success_ratio:24h has no series, say the 24h window had no Stage 2 rows. Do not call that 0% or a breach.
If any other tool has no series, say "no series". Do not substitute 0.

Fill only these lines. Skip a cause you cannot point at a tool result.

Stage 2 lag check
- Worker scrape (up{job="stage2_worker"}):
- stage2_queue last run: pending, empty, read failures (30m):
- Rows left pending by failed writes (30m): analysis upserts, status merges:
- Pace (not an SLO): accepted rows/s (5m), urls processed/s (5m):
- SLO Stage 2 success (24h): ratio, breach (yes/no/no series), rows by outcome:
- SLO QueueBacklog: max depth by queue, over 10000 (yes/no). Not stage2_queue:
- SLO Kafka consumer lag: max over 5m, over 250000 (yes/no), topic and group if present:
- Other series (not SLOs), only if the value is greater than 0: http fetch outcomes, retries, host throttled, oversized by kind, gather exception class, stage error_type:
- Not explained by these series: say so. Do not guess a host, a site, or a page.
```

## Tool list

Each tool is one instant query. Range is only the range selector inside the template. No extra matchers.

### 1. `stage2_worker_up`

Scrape health for the Stage 2 worker job. Not an SLO. `0` means Prometheus cannot scrape `job="stage2_worker"` ([`prometheus.yml`](../../Scraping_project/monitoring/prometheus.yml)).

```promql
up{job="stage2_worker"}
```

### 2. `stage2_queue_pending`

Pending `stage2_queue` rows at the start of the last run. Not an SLO. Do not compare the value to 10000.

```promql
stage2_queue_pending
```

### 3. `stage2_queue_empty`

`1` when that last run saw no pending rows, else `0`. An empty queue is normal when Stage 1 is idle. Not an SLO.

```promql
stage2_queue_empty
```

### 4. `stage2_queue_read_failures_30m`

Runs that could not read `stage2_queue` at all. Those runs look idle. Not an SLO.

```promql
increase(stage2_queue_read_failures_total[30m])
```

### 5. `stage2_analysis_write_failures_30m`

Failed `stage2_page_analysis` upserts. The queue rows stay pending. Not an SLO.

```promql
increase(stage2_analysis_write_failures_total[30m])
```

### 6. `stage2_queue_update_failures_30m`

Failed status merges, by the `status` label. Rows stay pending. Not an SLO.

```promql
sum by (status) (increase(stage2_queue_update_failures_total[30m]))
```

### 7. `stage2_accepted_rate_5m`

Pace of accepted rows. Not an SLO. No target rate is published.

```promql
sum(rate(stage2_rows_total{outcome="accepted"}[5m]))
```

### 8. `stage2_urls_processed_rate_5m`

Pace from the stage sink ([`metrics_sink.py`](../../Scraping_project/src/utils/metrics_sink.py)). Not an SLO. `stage="stage2"` is the label the sink sets, not a site.

```promql
sum(rate(scrapy_stage_urls_processed_total{stage="stage2"}[5m]))
```

### 9. `stage2_success_ratio_24h`

The Stage 2 success SLO, as the recording rule `stage2:success_ratio:24h` ([`recording_rules.yml`](../../Scraping_project/monitoring/recording_rules.yml)). Objective: at least 99% over 24h. No series means the window had no rows, which is not a breach and not 0%.

```promql
stage2:success_ratio:24h
```

### 10. `stage2_success_slo_breach`

The shipped `Stage2SuccessSLO` expression ([`alerting_rules.yml`](../../Scraping_project/monitoring/alerting_rules.yml)). `1` means the 24h ratio is under 99% and the window had rows. This is the same SLO as tool 9, not a second objective.

```promql
(
  stage2:success_ratio:24h < 0.99
)
and
(
  sum(increase(stage2_rows_total[24h])) > 0
)
```

### 11. `stage2_rows_by_outcome_24h`

What spent the 1% error budget. `outcome="accepted"` is `stage2_page_analysis`; other outcomes, including `quarantined`, are non-accepted. Not a new SLO.

```promql
sum by (outcome) (increase(stage2_rows_total[24h]))
```

### 12. `redis_queue_backlog`

The QueueBacklog SLO. Breach when the value is above 10000. The series that moves is the JS priority queue (`queue="priority_queue"`), not `stage2_queue`.

```promql
max by (queue) (redis_queue_length)
```

### 13. `kafka_consumer_lag_max_5m`

The Kafka consumer lag SLO. Breach when the value is above 250000 messages. Report `topic` and `group` when those labels are present. Do not compare to 10000 or 100000; those are earlier warnings, not this objective.

```promql
max by (topic, group) (max_over_time(kafka_consumergroup_lag_max[5m]))
```

`kafka_consumergroup_lag_max` is the recording rule over JMX `kafka_consumer_records_lag`. The `max by` keeps the labels the alert already describes. It does not change the 250000 objective.

### 14. `stage2_http_fetches_30m`

Fetch outcomes on the worker counter: `first_try`, `recovered`, `exhausted`, `circuit_open`. Not an SLO. `circuit_open` means the existing per-host breaker deferred the URL. It is not a site cap.

```promql
sum by (outcome) (increase(stage2_http_fetches_total[30m]))
```

### 15. `stage2_http_retries_30m`

Retry attempts by the `reason` label (`timeout`, `connection`, `http_5xx/408`). Not an SLO.

```promql
sum by (reason) (increase(stage2_http_retries_total[30m]))
```

### 16. `stage2_host_throttled_30m`

Fetches that waited for the per-host concurrency slot the worker already applies. Not an SLO. This design does not add a site cap or change that slot.

```promql
increase(stage2_host_throttled_total[30m])
```

### 17. `stage2_oversized_30m`

Oversized fetches by `kind`. Not an SLO. Do not propose a new byte cap in the summary. The byte limits stay in the worker.

```promql
sum by (kind) (increase(stage2_oversized_skipped_total[30m]))
```

### 18. `stage2_gather_exceptions_30m`

Exceptions that escaped URL analysis inside `asyncio.gather`, by exception class name. Not an SLO.

```promql
sum by (exception) (increase(stage2_gather_exceptions_total[30m]))
```

### 19. `stage2_errors_by_type_30m`

Per-URL Stage 2 errors from the dual-exported sink, by `error_type`. Not an SLO. The label is the truncated type string already on the counter. Do not join it to a URL.

```promql
sum by (error_type) (increase(scrapy_stage_errors_total{stage="stage2"}[30m]))
```

## How to read a lag answer

Lag is not one gauge. Read the tools in three groups:

1. **Work is waiting or stuck.** `stage2_queue_pending` is high, or read failures, analysis upsert failures, or status-merge failures are non-zero. The queue is Delta. Redis depth does not measure it.
2. **Work is failing the success objective.** Tool 10 is 1. Tool 11 shows which `outcome` is non-accepted. Tool 9 with no series is idle, not a failure.
3. **An upstream SLO is also breached.** Tool 12 above 10000 is the JS queue. Tool 13 above 250000 is Kafka consume lag. Either can sit in front of later stages. Neither is `stage2_queue` depth.

If the worker is up, pending is not stuck behind the failure counters, the success series is absent or at least 99%, and the other two SLOs are within their lines, the summary says these series do not explain the lag. It stops there.

## Privacy

The tools return aggregates and the labels named on each template: `job`, `queue`, `status`, `outcome`, `topic`, `group`, `reason`, `kind`, `exception`, `error_type`, `stage`. That is the whole result the summary may quote.

- Do not interpolate the question, a hostname, a URL, or any other operator text into PromQL.
- Do not add matchers. In particular do not group or filter by `url`, `url_hash`, `host`, `error_message`, `cookie`, or `authorization`.
- Do not read Delta (`stage2_queue`, `stage2_page_analysis`, `stage2_errors`), Postgres `error_logs` or `performance_metrics`, Redis members, or worker logs. Those hold page URLs, and error text can quote a URL. Do not read the metrics-sink fallback log either. Its payload can contain a URL.
- `exception` is a class name. `error_type` is the existing counter label, already truncated in the sink. Quote it as a count key, not as a message, and do not look up the matching row.
- If a sample arrives with a label outside the list above, drop the sample. Do not put it in the summary.
- The summary stays in the operator session that asked. It is not written to a table, not attached to crawl output, and not sent off the operator network.
- This design calls no model API and stores no prompt. Control Center's existing access check, if enabled, is unchanged. No new credential is created.
- A later model, if any, may see only the prompt in this file and the tool results that survived the label rule. It may not see lake rows, logs, or Redis. No such model is approved here.

## Out of scope

- A production LLM, an API key, a prompt store, or a vendor call.
- Editing alerts, recording rules, dashboards, `config.yml`, the README, or worker code.
- A fourth SLO, a crawl-freshness number, a minute-valued Kafka lag, a burn-rate multiplier, or a site cap.
- Teaching Control Center to evaluate PromQL. That would be a separate change to the dashboard server, which this issue does not authorize.
