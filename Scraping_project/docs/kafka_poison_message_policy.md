# Kafka poison-message policy (ingest)

A malformed payload on the Kafka to Delta path must not block its partition.
This is the consumer-side rule (#177). It is not a fourth pipeline SLO.

## Consumer entrypoint

`async fn ingest` in
[`Scraping_project/kafka-delta-ingest/src/main.rs`](../kafka-delta-ingest/src/main.rs).

The process is `kafka-delta-ingest ingest`. The image entrypoint execs that
binary. Helm passes the flags from `kafkaDeltaIngestor.config` in
[`k8s/helm/scraping-pipeline/values.yaml`](../k8s/helm/scraping-pipeline/values.yaml)
through
[`templates/kafka-delta-ingestor-deployment.yaml`](../k8s/helm/scraping-pipeline/templates/kafka-delta-ingestor-deployment.yaml):

| Flag | values.yaml | Default |
| --- | --- | --- |
| `--dlq-topic` | `kafkaDeltaIngestor.config.dlqTopic` | `scraped-items-dlq` |
| `--max-deserialize-failures` | `kafkaDeltaIngestor.config.maxDeserializeFailures` | `3` |

These keys are not in `config.yml`.

## What is not this policy

[`SchemaValidationPipeline`](../src/pipelines.py) validates items in the spider
and publishes producer-side failures to `validation_failures`. That topic does
not commit the ingest consumer's offset. A payload that still arrives on
`scraped-items` (bad JSON, schema miss, empty body) is handled here. A `schema_id` header other than the published id `1` (`scraped-items-value`) is `unknown_schema_id` and takes the same path (#654). A missing header is still accepted.

## Rule

`poison_action` in `main.rs` (and `src/utils/kafka_poison_policy.py`, the same
rule) takes the 1-based failure count for the payload being deserialized:

1. Below the limit, the decision is **retry**. The offset is not committed and
   the payload is not published. The ingestor deserializes that same delivery
   again, up to the limit. It does not seek and it does not read the next
   offset while this one is still retrying.
2. At the limit (minimum 1, so `0` cannot mean "retry forever"), the decision
   is **DLQ and commit**. The original bytes are produced to `--dlq-topic`
   with the existing dead-letter headers (`dlq.reason`, `dlq.source.topic`,
   `dlq.source.partition`, `dlq.source.offset`, and the rest from #544).
   After the broker acks that produce, the offset is recorded and flushed:
   - If good rows are buffered, they are written to Delta first, then the
     commit covers them and the poison offset (at-least-once, #282).
   - If the buffer is empty, the offset is committed immediately. The
     consumer does not wait for another record or for `allowed-latency`.

One malformed message therefore cannot sit uncommitted at the end of a
partition, and it cannot stop the following offset from being read.

If the DLQ produce still fails after `--max-write-attempts`, the process exits
**without** committing (#544). The payload is not dropped on the floor. That
outage is the DLQ being unreachable, not an unbounded deserialize retry.

Accepted rows are unchanged: their offsets commit only after the Delta batch
that contains them.

## Alert

`KafkaPoisonMessageRate` in `monitoring/alerting_rules.yml` (the Helm copy is
the same file) fires when

```
sum(rate(kafka_delta_ingest_messages_dead_lettered_total[5m])) > 0.1
```

for 5 minutes. `messages.dead_lettered` is counted only after the DLQ produce
is acked. The series is `kafka_delta_ingest_messages_dead_lettered_total` via
`statsd_mapping.yml`. This is a warning on the poison/DLQ rate. It is not an
SLO. `Stage2SuccessSLO`, `QueueBacklog` (`> 10000`) and `KafkaConsumerLagSLO`
(`> 250000`) are not part of this rule.
