# ADR-0004: Retracted URLs are soft-deleted in place (`is_deleted`)

- **Status:** Proposed
- **Date:** 2026-10-15
- **Issue / PR:** #417

## Context

Legal takedowns, robots.txt disallows and crawl corrections must stop reaching
silver and gold consumers. The lake's write paths (append, overwrite, MERGE in
`Scraping_project/src/lakehouse/lakehouse_manager.py`) have no retraction
convention: no `is_deleted` or tombstone column, and `BaseRecordSchema` has no
retraction fields. A bad URL stays queryable until someone overwrites it by hand.

Readers outside this repository query the Delta tables directly
([ADR-0003](0003-delta-schema-registry.md)). Whatever marks a retraction is
therefore a consumer-facing contract, and it is costly to change later.

## Decision

We will soft-delete a retracted URL in place. `LakehouseManager.tombstone_urls`
sets `is_deleted`, `deleted_at` and `deleted_reason` on every existing row with
that `url_hash`, using an update-only Delta MERGE. Readers filter with
`NOT COALESCE(is_deleted, FALSE)`. This implies:

1. The input record is `TombstoneRecord` (`url_hash`, `deleted_at`, `reason`) in `Scraping_project/src/schemas.py`.
2. The added columns are nullable and additive. A table that has never been retracted has no `is_deleted` column.
3. This path never removes rows. It rewrites only the data files that hold a retracted row. Older versions stay readable until VACUUM removes them.
4. The filter is documented in `docs/contracts/README.md`. The columns are not added to the registered JSON Schemas, so no contract version changes.

## Consequences

- Easier: a retraction is one MERGE per table. It does not rewrite the table's history, the writers' schemas stay the same, and the audit trail is kept.
- Harder: every consumer must add the filter. A reader that skips it still shows retracted rows.
- Harder: this is a logical retraction. Time travel and un-vacuumed files still hold the content until retention passes. A takedown that requires physical removal needs a separate purge path.
- Harder: a row appended later for the same `url_hash` is live until the retraction runs again. Closing that gap means writers consult the tombstones, which is follow-up work.
- Revisit this decision if takedowns must remove content from storage, or if readers find a separate tombstone table simpler.

## Alternatives considered

- **Separate tombstone table, anti-joined by readers:** leaves existing tables untouched, but every consumer must learn an anti-join and the rows still need filtering. Not chosen, because the mark on the row keeps each table as its own source of truth.
- **Physical `DELETE` plus `VACUUM`:** removes the content, but breaks the replay and audit history the stages rely on, and `VACUUM` is bound by retention. Not chosen as the default. A future purge path can add it.
- **Overwrite the table without the retracted rows:** rewrites all history and races concurrent writers, which the MERGE path (#169) exists to avoid. Not chosen.
