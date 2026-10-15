# ADR-0004: Nullable `tenant_id` on every lake table

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #670

## Context

Site metrics (#192) and Redis seen-sets (#191) can already name a site, but Delta rows could not. A later customer or org split would have to rewrite every table or guess the tenant from a URL. The lake is still a single UConn profile. Isolation is not enforced yet.

## Decision

We will add a nullable string column `tenant_id` to every registered lake schema and stamp it on new Python lake writes.

1. The crawl profile sets the value: `DELTA_TENANT_ID`, else `delta_lake.tenant_id` in `config.yml`, else `default`.
2. A caller may set `tenant_id` on a row. That value is kept when it matches the site-id alphabet.
3. Existing files are not rewritten. The next append adds the column. Older rows read as null, and the query helper treats those nulls as `default`.
4. The column is not a partition key and writes are not rejected for the wrong tenant.

`docs/TENANT_ID.md` is the migration note. `kafka-delta-ingest` keeps its bronze column list; its rows stay null until that writer learns the column.

## Consequences

- New tables and evolved tables can be filtered by tenant without a second lake.
- Historical rows stay visible to the `default` tenant until someone runs the documented backfill.
- A second tenant that must not see those nulls has to backfill or pass `include_legacy=False` and accept that pre-#670 rows disappear from that query.
- Revisit this when a real second tenant needs enforced isolation, partition pruning, or the Rust ingestor to stamp the same id.

## Alternatives considered

- **Partition every table by `tenant_id`:** delta-rs cannot repartition an existing table in place, and queue tables are upserted in place. A nullable column does not force that migration.
- **Required column with no nulls:** schema evolution cannot add a non-nullable column onto files that lack it, and a backfill on deploy rewrites the lake.
- **Reuse `site_id` as the only name:** metrics already use `site_id` as a label. The lake column is `tenant_id` so a later org id is not tied to the Redis key prefix. The shipped default matches the single-tenant site id `default`.
