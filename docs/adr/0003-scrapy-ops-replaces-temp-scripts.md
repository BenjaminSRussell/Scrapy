# ADR-0003: `scrapy-ops` is the lake ops entrypoint; `temp_scripts/` is retired

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #1098

## Context

`temp_scripts/` at the repo root and `Scraping_project/temp_scripts/` held one-off
experiments (pipeline starters, mock servers, ad-hoc tests). They were already
documented as unsupported, and each had a supported replacement (`start.py`,
`shutdown.py`, `cli.py`, `diagnose.sh`, `scripts/`, `dashboard/serve.py`, `tests/`).
Delta compact and vacuum lived on `LakehouseManager` and `cli.py lake-vacuum`,
with a deployment doc that still pointed at a module that does not exist
(`src.utils.maintenance`). Operators had no single command for compact, vacuum,
and a health check.

## Decision

We will use **`scrapy-ops lake compact|vacuum|doctor`** as the lake ops CLI, and
we will not keep `temp_scripts/`.

1. The implementation is `src/ops/scrapy_ops.py`. From `Scraping_project/`, run
   `python scripts/ops/scrapy-ops`. An installed package exposes the same
   `scrapy-ops` console script.
2. `lake vacuum` previews and deletes nothing unless `--apply` is passed, matching
   `cli.py lake-vacuum`. `cli.py lake-vacuum` stays.
3. `lake doctor` always runs three checks: Redis `PING` via `REDIS_HOST`,
   `REDIS_PORT`, `REDIS_PASSWORD` and `REDIS_DB` (not `REDIS_URL`); the Kafka
   topics `config.yml` declares; and Delta health. A missing lake path, or a
   table directory that has data files but no `_delta_log`, fails the command.
   Empty table directories that have never been written are not failures.
4. `doctor` does not create lake directories and does not create Kafka topics.
5. CI (`main.yml` and `tests/unit/test_scrapy_ops_1098.py`) fails if any file
   appears under `temp_scripts/` or `Scraping_project/temp_scripts/`.

## Consequences

- One documented command for compaction, vacuum, and the three operator checks.
- The old scripts are gone. Recreating them fails CI instead of becoming a
  second, untested entrypoint.
- `doctor` needs a reachable Redis and Kafka to exit 0. That is intentional:
  a down dependency is a failed check, not a skip.
- Revisit if lake maintenance moves into a scheduler. The CLI should stay the
  manual entrypoint that the scheduler calls.

## Alternatives considered

- **Move every `temp_scripts/` file under `scripts/ops/`:** those scripts target
  removed modules, ports, and `docker-compose` invocations. Keeping them would
  preserve the wrong-tool problem this decision exists to stop.
- **Only document `cli.py lake-vacuum`:** compact and the Redis/Kafka/Delta
  doctor check would still have no shared command, and the deployment doc would
  keep pointing at a missing module.
