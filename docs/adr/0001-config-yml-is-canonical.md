# ADR-0001: `Scraping_project/config.yml` is the canonical configuration

- **Status:** Accepted
- **Date:** 2026-10-08
- **Issue / PR:** #715 (records the practice already used in `src/core/config.py` and `src/settings.py`)

## Context

Configuration used to be spread across `src/common/config.py`,
`src/common/config_manager.py`, per-environment `config/{ENV}.yml` files, Scrapy's
`settings.py` constants, and ad-hoc environment variables. Different entry points
could read different values for the same setting. Helm, compose, and local runs drifted.

## Decision

We will treat **`Scraping_project/config.yml`** as the single source of truth for
application configuration.

1. All Python code reads configuration through `src.core.config.get_config()`
   (`Config` loads `config.yml` relative to the project root, hands out copies, and
   keeps the previous snapshot live if a reload fails to parse).
2. Scrapy settings are derived from it in `src/settings.py`, using an explicit
   `scrapy:` section first, then values bridged from other sections, then code defaults.
3. **Environment variables override `config.yml` only for deployment-specific values**
   (hosts, ports, credentials, paths such as `REDIS_HOST`, `KAFKA_BOOTSTRAP_SERVERS`,
   `DELTA_LAKE_PATH`, `LOG_LEVEL`). Precedence is environment variable, then
   `config.yml`, then the code default. Secrets never go in `config.yml`; they come from
   the environment (`.env` locally, Kubernetes Secrets in Helm).
4. `config/{ENV}.yml` per-environment files are not used. `Scraping_project/config/`
   holds only opt-in examples (for instance `entity_summarization.example.yml`).
5. A new tunable gets a key in `config.yml` with a comment, and a code default that
   matches it.

## Consequences

- One file to read when asking "what does production do?". Reviews of behaviour
  changes include the `config.yml` diff.
- `config.yml` is a hot shared file. Unrelated PRs touching it conflict more often,
  so keep keys grouped by section and edits minimal.
- Helm and compose must pass deployment values as environment variables instead of
  shipping alternate config files.
- Revisit if per-tenant or per-crawl configuration is needed. That would call for
  layered files, not more environment variables.

## Alternatives considered

- **Per-environment YAML (`config/dev.yml`, `config/prod.yml`):** caused the drift
  described above, and most differences were hosts and credentials, which the
  environment already carries.
- **Environment variables only:** too many nested tunables (stage thresholds,
  sitemap caps, seed domain tables) to express as flat variables, and no reviewable diff.
