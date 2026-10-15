# ADR-0004: Scraped-item schema ids use a local registry stub

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #654

## Context

Issue #231 versioned the scraped-item contract. Python and the Rust ingestor
can still drift, and there is no Schema Registry in the default Compose
profiles. A live Confluent registry would be another service on the core path
and is not required to name the JSON schema producers already emit.

## Decision

We will publish scraped-item JSON schema id `1` for Confluent subject
`scraped-items-value` from a local stub (`SCRAPED_ITEM_SCHEMA_REGISTRY` in
`src/schemas.py`, mirrored by `SCRAPED_ITEM_SCHEMA_ID` in
`kafka-delta-ingest`).

1. `KafkaPipeline` sets a `schema_id` header to `1`. The message body stays
   JSON, so spill files and existing consumers keep parsing it.
2. The ingestor accepts a missing header (messages produced before this stub).
   Any other id, including a non-integer, is `unknown_schema_id` and follows
   the existing dead-letter path.
3. Compose profile `schema-registry` documents a Confluent-compatible server
   (`confluentinc/cp-schema-registry:7.7.1` on `127.0.0.1:8081`). It is not on
   `core` or `streaming`, and `start.py` does not enable it. The stub does
   not call that server.

## Consequences

Producers and the ingestor can reject an unexpected schema id without a
registry process. Changing the published id is a contract change: both
constants and the header must move together. The Compose service is an
example only; registering the subject there is follow-up work. Revisit if a
real registry becomes required, or if the body must use the Confluent magic
byte plus schema id (that would stop raw JSON consumers and spill replay).

## Alternatives considered

- **Magic-byte wire format on every payload:** Confluent's serializer prefix.
  Rejected for this stub. It would make current JSON payloads and spill files
  unreadable, and it would reject in-flight messages that have no prefix.
- **Require the header and reject a missing id:** stricter, but every message
  already on `scraped-items` would be dead-lettered.
- **Run Schema Registry on `streaming`:** puts an extra image on the Kafka
  profile. The example stays opt-in.
