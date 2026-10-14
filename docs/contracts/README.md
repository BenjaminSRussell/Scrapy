# Delta schema registry

Cross-repo column contracts for the core Delta tables this pipeline writes.
`car-datalake`, Data-visualizer, and agent-coordination should pin these files
instead of copying column lists out of worker code.

| Contract | Delta table | What it is |
|---|---|---|
| [pages](pages.schema.json) | `stage2_page_analysis` | Stage 2 page analysis |
| [extractions](extractions.schema.json) | `metadata_queue` | Keyword and entity extraction |
| [jobs](jobs.schema.json) | `stage2_queue` | Stage 2 crawl work items |

The contract id is not a directory name. Writers still use the Delta table
name. [registry.json](registry.json) is the index.

This is not a medallion gold layer. Bronze, silver, and gold names stay the
open work in #303. The federated gold query
([docs/product/federated-tenant-gold-query.md](../product/federated-tenant-gold-query.md))
still has no gold tables to open. Do not point it at these stage tables.

`LakehouseManager` also stamps `_ingestion_time` and `_stage` on every write.
Those columns are not part of the contract. Schemas set
`additionalProperties: true` so a reader can ignore them.

## What CI checks

`Scraping_project/scripts/check_delta_contracts.py` (stdlib only) runs in the
`CI Delta Contracts` workflow on pull requests that touch a contract, a
registered writer, or `src/core/schemas.py`. It fails when:

- a `*.schema.json` in this directory is missing from `registry.json`, or the reverse
- `x-contract-version` is not `MAJOR.MINOR.PATCH`
- a required field is missing from the writer function that claims to emit it
- a non-derived property is missing from `SCHEMA_REGISTRY` in `src/core/schemas.py`, or its `x-arrow` type disagrees with that registry
- a derived partition column is marked required (writers do not set those)

The check is static. It does not read a lake, start Spark, or crawl.

Arrow types and writer values disagree for a few timestamps: the PyArrow
registry says `timestamp(ms)`, and the workers pass ISO-8601 strings.
`LakehouseManager` does not apply `SCHEMA_REGISTRY` on write; it infers the
column from the batch. The JSON Schema `type` follows the writer. `x-arrow`
follows the registry so a later change to either side is visible.

## Version bump policy

Each file has its own `x-contract-version` (SemVer). Bump that version in the
same change as the schema. Project versioning stays
[docs/RELEASING.md](../RELEASING.md). While the project is `0.y.z`, a breaking
project bump is MINOR and any other project bump is PATCH.

| Contract change | Contract bump | Project bump |
|---|---|---|
| Documentation only, no property or type change | none | none |
| New optional property, or a wider type that still accepts the old value | MINOR | MINOR (PATCH while `0.y.z`) |
| Remove, rename, or retype a property; make an optional property required; narrow a type; change `x-delta-table` | MAJOR | MAJOR (MINOR while `0.y.z`) |

A contract MAJOR is a breaking change for downstream readers. Call it out
under `### Changed` in `CHANGELOG.md` and say what those readers must do.
Adding a column to a Delta table is still additive for existing files;
removing or retyping one is not, and needs an explicit `schema_overwrite`
as described in `Scraping_project/README.md`.

`registry_version` in `registry.json` bumps MAJOR when a contract is removed
or a contract id changes, and MINOR when a contract is added.

## Adding a table

1. Copy an existing schema. Set `title`, `x-delta-table`, and `x-contract-version` to `1.0.0`.
2. List every stable column. Mark partition columns `x-derived: true` and name them in `x-partition-columns`. Do not require a derived column.
3. Point `x-writers` at the function that builds the row. Use `covers` when one function emits only some required fields; the union must cover every required field.
4. Add the file to `registry.json`.
5. Run `python3 Scraping_project/scripts/check_delta_contracts.py`.

Decision record: [ADR-0003](../adr/0003-delta-schema-registry.md).
