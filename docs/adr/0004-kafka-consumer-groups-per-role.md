# ADR-0004: Kafka consumer groups are per role

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #624

## Context

`kafka.consumer.group_id` was a single id, `scraping-pipeline`. The lake ingestor
did not use it. Helm passes `--app-id scrapy-ingestor`, and the `kafka-delta-ingest`
default was `kafka-delta-ingest`, a name that is not a pipeline role. A stage
consumer added later would either join that one group (and move its offsets) or
pick an unrelated id that lag alerts could not tell apart. Replay of Stage 2
or of the ingest topic has to seek without shifting the lake cursor. Commit
policy (#252, #282) is a separate decision and is unchanged.

## Decision

We will give each incompatible processor its own `group.id`, configured in
`Scraping_project/config.yml` `kafka.consumer.groups`:

1. `ingest` is `scrapy-ingestor`. That is the id Helm already uses, so this
   decision does not start a new ingest group on the chart.
2. `dq` is `scrapy-dq`. A data-quality reader must not commit the ingest cursor.
3. `replay` is `scrapy-replay`. An independent re-read must not commit the
   ingest or DQ cursor.
4. `kafka.consumer.group_id` stays `scraping-pipeline` and is retired. No role
   may use it. Config validation and the ingestor both reject a shared id.
5. The ingestor sets `client.id` to `{group.id}-{pid}`. Lag rules already label
   alerts by the `group` derived from that shape. `KafkaConsumerLagSLO` stays
   above 250000 messages.

A new ingest group id is a new cursor. Do not change `groups.ingest` or Helm
`appId` unless re-reading the topic is intended.

## Consequences

- Replay and DQ can start at `earliest` without rewinding lake ingest.
- Lag for each role is a separate series. A DQ backlog does not look like ingest.
- A bare `kafka-delta-ingest` that used the old default `kafka-delta-ingest`
  does not resume those offsets. Helm is unaffected.
- Adding a fourth processor means a new role key, not reuse of these three.

## Alternatives considered

- **Keep one shared group.** Rejected. Independent replay is impossible.
- **Change the live Helm id to a new string.** Rejected. `auto.offset.reset` is
  `earliest`, so a new id re-reads `scraped-items` into the lake.
- **Encode the role only in `client.id`.** Rejected. Offsets follow `group.id`,
  not the lag label.
