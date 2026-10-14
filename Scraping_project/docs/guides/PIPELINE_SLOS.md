# Pipeline SLOs

Numeric objectives for the crawl pipeline (#179). Each target below is copied from the issue text or from an alert that already fires. Nothing here retunes `QueueBacklog`, the Redis watermarks, or `KafkaConsumerLagSLO`.

Crawl freshness is not an SLO. The issue names it, but the issue text, `monitoring/alerting_rules.yml`, and `DEPLOYMENT.md` do not give a freshness number, so none is published.

`monitoring/alerts.yml` is not a Prometheus rule file and is not recreated. Prometheus loads `recording_rules.yml` and `alerting_rules.yml` only.

## Targets

| SLO | Target | Query | Alert | Panel |
|---|---|---|---|---|
| Stage 2 success | >= 99% of rows over 24h | `stage2:success_ratio:24h` | `Stage2SuccessSLO` | Pipeline SLOs → Stage 2 success (24h) |
| Redis queue depth | max depth stays <= 10000 | `max by (queue) (redis_queue_length)` | `QueueBacklog` (unchanged) | Pipeline SLOs → Redis queue depth |
| Kafka consume lag | max lag stays <= 250000 messages | `max_over_time(kafka_consumergroup_lag_max[5m])` | `KafkaConsumerLagSLO` (unchanged) | Pipeline SLOs → Kafka consumer lag |

The dashboard is `monitoring/dashboards/pipeline_slos.json` (uid `pipeline-slos`), provisioned with the other JSON files in that directory. Helm carries the same copy.

## Stage 2 success

**Target:** success >= 99% over 24h. That figure is the example in issue #179. It is not taken from an older alert.

**Query** (`monitoring/recording_rules.yml`, group `pipeline_slos`):

```promql
# stage2:success_ratio:24h
(
  sum(increase(stage2_rows_total{outcome="accepted"}[24h])) or vector(0)
)
/
sum(increase(stage2_rows_total[24h]))
```

`stage2_rows_total{outcome}` counts accepted rows (`stage2_page_analysis`) and quarantined rows (`stage2_errors`). The denominator is every outcome, so a new non-accepted label spends budget too. When the window has no rows the recording rule emits nothing (an idle worker is not 0%).

**Alert:** `Stage2SuccessSLO` in `monitoring/alerting_rules.yml` group `pipeline_slos`, and the same expression in `monitoring/alerting/rules.yml` (Grafana). It is a threshold on the SLO window, not a separate burn multiplier:

```promql
(stage2:success_ratio:24h < 0.99)
and
(sum(increase(stage2_rows_total[24h])) > 0)
```

`for: 15m` is the same hold as `KafkaConsumerLagSLO`. It is not a new objective. The alert does not fire when Stage 2 recorded no rows.

**Error budget.** 99% success leaves a 1% budget: 1% of `stage2_rows_total` over the same 24h may be non-accepted. The budget is exhausted when `Stage2SuccessSLO` fires. No 2x or 14.4x burn rate is published in the rules, `DEPLOYMENT.md`, or the issue, so none is added. Redis and Kafka are threshold objectives, not ratio budgets; they do not get a made-up percentage.

## Redis queue depth

**Target:** a queue is over its objective when `max by (queue) (redis_queue_length) > 10000`. That is the existing `QueueBacklog` expression (`monitoring/alerting_rules.yml`). The issue's "p99 < X" is not used: no p99 and no other depth number are published, and this rule is not changed.

The same 10000 is the producer high watermark (`DEFAULT_QUEUE_HIGH_WATERMARK` in `src/utils/redis.py`). The low watermark stays 8000 and the mode stays `block`. `redis_queue_backpressure_total{queue,action}` already counts block and shed events. Those knobs are not part of this change.

**Query:**

```promql
max by (queue) (redis_queue_length)
```

**Alert:** `QueueBacklog`, `for: 10m`, severity warning. Grafana colors the panel red at 10000, the same threshold.

## Kafka consume lag

**Target:** max lag stays at or below 250000 messages. That is `KafkaConsumerLagSLO`:

```promql
max_over_time(kafka_consumergroup_lag_max[5m]) > 250000
```

`for: 15m`, severity critical, label `slo: "true"`. The rule is not changed.

There is no minute-valued lag SLO in `alerting_rules.yml`, `DEPLOYMENT.md`, or the issue (the issue writes "Kafka lag < Y minutes" with Y unset). This SLO is the message threshold above, not a new minute value. `KafkaHighConsumerLag` (10000) and `KafkaVeryHighConsumerLag` (100000) are earlier warnings, not this objective.

**Query:**

```promql
max_over_time(kafka_consumergroup_lag_max[5m])
```

`kafka_consumergroup_lag_max` is the recording rule that turns JMX `kafka_consumer_records_lag` into a per-group max.

## Where the numbers come from

| Number | Source |
|---|---|
| Stage 2 >= 99% over 24h | Issue #179, proposed standard ("Stage2 success >= 99% over 24h"). Not an older alert. |
| 1% error budget | The same 99% target (1 - 0.99). No other budget percentage is published. |
| Redis depth > 10000 | `monitoring/alerting_rules.yml` `QueueBacklog` (`max by (queue) (redis_queue_length) > 10000`). On origin/main before this change that expression was line 669; it is unchanged. |
| High 10000, low 8000, mode block | `src/utils/redis.py` `DEFAULT_QUEUE_HIGH_WATERMARK` (line 213), `DEFAULT_QUEUE_LOW_WATERMARK` (line 214), `DEFAULT_BACKPRESSURE_MODE` (line 215). Not changed. |
| Kafka lag > 250000 messages | `monitoring/alerting_rules.yml` `KafkaConsumerLagSLO` (`max_over_time(kafka_consumergroup_lag_max[5m]) > 250000`). On origin/main before this change that expression was line 121; it is unchanged. There is no minute threshold to copy. |

## What this does not change

- `QueueBacklog` expression, `for`, labels, and annotations.
- Redis watermarks: high 10000, low 8000, mode `block`. `admit_enqueue` is untouched.
- `KafkaConsumerLagSLO` expression and threshold.
- Seen-set key shape, `cbstate:` / `cbfail:`, and `CIRCUIT_BREAKER_SHARED=0`.
- `monitoring/alerts.yml` stays absent.
