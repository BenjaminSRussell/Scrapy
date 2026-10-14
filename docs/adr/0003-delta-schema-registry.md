# ADR-0003: Cross-repo Delta contracts live in `docs/contracts`

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #1099

## Context

Stage 2 page analysis, metadata extraction, and the Stage 2 queue are read
outside this repository. Column names have drifted before between the Python
producer, the Rust ingestor, and `SCHEMA_REGISTRY` (see #227 and #531). There
was no versioned document those readers could pin, and nothing failed a pull
request when a writer stopped emitting a column the registry still listed.

Medallion gold tables are not in the tree (#303). This decision does not
create them and does not relabel stage tables as gold.

## Decision

We will keep a JSON Schema contract per core consumer table under
`docs/contracts/`, indexed by `registry.json`.

1. The first contracts are `pages` (`stage2_page_analysis`), `extractions`
   (`metadata_queue`), and `jobs` (`stage2_queue`). The contract id is not a
   Delta path.
2. `x-contract-version` is SemVer per file. The bump rules are in
   `docs/contracts/README.md` and line up with `docs/RELEASING.md`.
3. A stdlib script checks each contract against the writer functions named in
   `x-writers` and against `SCHEMA_REGISTRY`. The `CI Delta Contracts`
   workflow runs that script on pull requests that touch the contracts or
   those writers.
4. Partition columns the lakehouse derives (`domain`, `date`) are documented
   on the contract and are not required writer fields.
5. `SCHEMA_REGISTRY` stays the in-process Arrow declaration. Writes still
   infer or cast from the table's own log, as `LakehouseManager` does today.
   Where a worker emits an ISO-8601 string and the Arrow declaration says
   `timestamp(ms)`, the JSON Schema type follows the writer and `x-arrow`
   records the declaration.

## Consequences

- Downstream repos can review a column change in this directory instead of
  diffing worker dicts.
- A pull request that drops a required writer key fails before merge.
- The check is static. It does not prove a live lake matches the contract.
- `additionalProperties` stays true because the lakehouse adds `_ingestion_time`
  and `_stage`, and scout adds handoff fields that are not stable.
- Revisit when #303 lands a real gold layer. Those tables need their own
  contracts. They should not reuse these three ids for a different path.

## Alternatives considered

- **Avro only:** the Rust ingestor and the bronze record already use JSON
  Schema. A second IDL would drift the same way the two bronze validators did.
- **Treat `SCHEMA_REGISTRY` as the published contract:** it is not applied on
  write, it uses Arrow types the workers do not emit, and other repositories
  should not import this package to read a column list.
- **Generate the schemas from the first Delta commit:** that needs a lake and
  would freeze inferred types, including one-off scout columns, as the contract.
