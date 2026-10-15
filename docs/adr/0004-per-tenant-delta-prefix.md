# ADR-0004: Per-tenant Delta paths are an opt-in directory prefix

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #671

## Context

One `delta_lake.base_path` (or `DELTA_LAKE_PATH`) holds every table. Two tenants
on that root share files. Hard isolation needs a path boundary. Moving the
default layout would orphan every existing table and every `kafka-delta-ingest`
path that already points at `{root}/{table}`.

Redis already has one site/tenant id (`SITE_ID`, else `TENANT_ID`, else
`REDIS_SITE_ID`, else `default`). A second id for the lake would split one
install into two namespaces. ADR-0001 keeps deployment-specific values in the
environment and `config.yml` as the canonical file.

The Delta PVCs are one claim each (`deltaLake.persistence.size` 500Gi,
`scrapyApp.persistence.deltaLake.size` 200Gi). Bytes per tenant were not measured.

## Decision

We will keep today's layout as the default and add an opt-in prefix.

1. `delta_lake.tenant_path_prefix` defaults to false. `DELTA_TENANT_PATH_PREFIX`
   overrides it (`true`/`false`, `1`/`0`, `yes`/`no`, `on`/`off`). Any other
   value raises. Unset means the config flag.
2. When the flag is on, `LakehouseManager` and `DeltaHelper` append one
   directory segment to a root they resolved themselves (`DELTA_LAKE_PATH`,
   else `delta_lake.base_path`). The segment is `resolve_site_id()` with no
   argument: the same id Redis seen-sets use. A value that is not one path
   segment (`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`) raises, and no directory is
   created.
3. An explicit `base_path` argument is not prefixed again. Callers that already
   name a directory (tests, tools, a helper handing its path to the manager)
   keep that directory. `kafka-delta-ingest` takes a table path on the command
   line and does not read this flag.
4. Nothing is moved automatically. Enabling the flag on a populated root
   creates empty tables under the prefix and leaves the old tree unread.
   Operators move table directories plus `_write_spill`, `_trash`, and `_audit`
   into `{root}/{tenant_id}/` while writers are stopped, and point the Rust
   ingestor at the prefixed table path. The steps are in the data-usage guide.
5. The flag does not add or resize a PVC. Tenants that share a claim share its
   bytes. Sizing notes stay in `Scraping_project/docs/REDIS_CAPACITY.md` and
   do not state a GB-per-tenant rate.

## Consequences

- A default install does not change paths, alerts, or claim sizes.
- Turning the flag on without the move looks like data loss. The move is manual
  and reversible only before new commits exist solely under the prefix.
- One process, one id. Two tenants still mean two processes (or two explicit
  paths), not one writer multiplexing tenants.
- Federation over gold lakes ([#770](https://github.com/BenjaminSRussell/Scrapy/issues/770))
  can use this prefix later. This decision does not define gold or a grant.
- Revisit if one process must write several tenants, or if the Rust ingestor
  should read the same flag instead of a table path.

## Alternatives considered

- **Always prefix, including `default`:** every existing lake would need a move
  on upgrade. Rejected.
- **A separate `delta_lake.tenant_id` that can differ from the Redis id:** a
  second id space. Rejected. The config file does not carry another id.
- **A PVC per tenant in the chart:** the issue asks for a path prefix and a
  sizing note, not a new claim. A tenant that must not share a volume already
  gets its own `DELTA_LAKE_PATH` with the flag left off.
