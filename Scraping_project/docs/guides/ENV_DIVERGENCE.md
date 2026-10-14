# Dev, stage, and prod without config overlays (#788)

Dev, stage, and prod share one application file, `Scraping_project/config.yml`.
They diverge through process environment variables. There is no
`CONFIG_ENV` loader and no `config/{ENV}.yml` deep-merge.

[ADR-0001](../../../docs/adr/0001-config-yml-is-canonical.md) (accepted)
records that decision. Per-environment YAML was tried earlier and the copies
drifted: hosts and secrets differed, and so did tunables that should have
been the same. This checklist is the operator procedure that replaces the
loader #788 asked for. Do not add the loader back.

## What the process actually loads

`Config` in `src/core/config.py` opens one YAML file. The default path is
`Scraping_project/config.yml`, next to the project root. `CONFIG_ENV` is not
read anywhere in this repository. A missing `config/dev.yml`,
`config/stage.yml`, or `config/prod.yml` is the correct state: nothing merges
those files, so the base file is the whole YAML config.

Passing a path to `get_config(path)` (only on the first call in the process)
**replaces** that file. It does not deep-merge onto `config.yml`. After the
singleton exists, a later path argument is ignored.

`Scraping_project/config/` holds opt-in feature examples, such as
`entity_summarization.example.yml`. It is not an environment directory. Do
not add `config/dev.yml` or `config/dev.yml.example` there; nothing would
load them, and a later reader would think `CONFIG_ENV=dev` works.

## `ENV` is not a config selector

`ENV` and `APP_ENV` do not choose a YAML file. `src/utils/destructive_guard.py`
reads them only for lake and queue wipes:

- Unset means `development` for that guard.
- `production` or `prod` refuses the wipe unless the operator passes
  `--i-know-what-im-doing`, sets `ALLOW_LAKE_RESET=1`, and types `production`.
- Any other value, including `staging`, stays on the non-production path.

Compose does not set `ENV`. The Helm application configmap sets
`ENV` from `scrapyApp.env.ENV`, which defaults to `production` in
`k8s/helm/scraping-pipeline/values.yaml`. That still does not load
`config/prod.yml`.

`values-prod.yaml` is a **Helm values** overlay (Grafana exposure, Kafka
replication). It is not an application config overlay. See
[k8s/README.md](../../k8s/README.md).

## Where a difference is allowed to live

| Difference | Where it lives | Do not |
|---|---|---|
| Hosts, ports, credentials, lake path, log level | Process environment. Names are in [CONFIGURATION.md](CONFIGURATION.md) and [`.env.example`](../../.env.example). | A second YAML file, or `${VAR}` inside `config.yml` (it is not expanded) |
| Thresholds, topic names, seed-domain tables, anything every deploy should share | `config.yml`, reviewed in git, with a matching code default | A copy of `config.yml` that exists only on one server |
| Chart exposure and Kafka durability | `values.yaml`, then `values-prod.yaml` for production | Pointing pods at `config/prod.yml` |
| How hard a wipe is to confirm | `ENV` or `APP_ENV` | Expecting that variable to select YAML |

Precedence for a key that has an environment variable is: that variable, then
`config.yml`, then the code default. A variable overrides only the key it
names.

## Checklist

Use this before pointing a new environment at the pipeline.

- [ ] The revision's `config.yml` is the one the process will open. No
      `config/prod.yml` (or `dev` / `stage`) sits beside it.
- [ ] `CONFIG_ENV` is unset. The loader ignores it, so a set value hides a
      mistake instead of changing config.
- [ ] Hosts, ports, passwords, and `DELTA_LAKE_PATH` are environment
      variables or a secret store, not a private edit of `config.yml` on
      that machine.
- [ ] Secrets (`DB_PASSWORD`, `REDIS_PASSWORD`, `KAFKA_SASL_PASSWORD`,
      `GRAFANA_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`) are not committed.
      See [SECURITY.md](../../../SECURITY.md).
- [ ] `REDIS_HOST` and `REDIS_PORT` are set. The crawler entrypoint requires
      both. Do not set `REDIS_URL` when `REDIS_HOST` is set; `REDIS_URL` is
      only a fallback.
- [ ] `DELTA_LAKE_PATH` is the lake for **this** environment (Compose uses
      `/data/delta`; a laptop default is `./data/delta_lake`). Workers and
      the metrics exporter must share it.
- [ ] Kafka: leave `KAFKA_BOOTSTRAP_SERVERS` unset on the core Compose
      profile (there is no broker). Set it only where the streaming profile
      or Helm runs. Helm sets `KAFKA_REQUIRE_IDEMPOTENCE=true`.
- [ ] Production hosts set `ENV=production` or `APP_ENV=production` so wipes
      need break-glass. Do not set `ENV` in order to load a YAML file.
- [ ] A Helm production install passes both values files:
      `-f values.yaml -f values-prod.yaml`. The application container still
      reads the image's `config.yml` plus the configmap environment.
- [ ] A new tunable is a key in `config.yml` (commented) and a code default
      that matches it. It is not a new environment file.

## Example: three environments, one file

This is the example file for #788. It is an environment matrix, not
`config/dev.yml.example`.

Local dev, in `.env` copied from `.env.example`. `ENV` stays unset, so the
destructive guard treats the machine as development. Core Compose has no
Kafka, so the bootstrap variable stays unset.

```bash
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
DB_HOST=localhost
DB_PORT=5432
DB_NAME=scraping_pipeline
DB_USER=scrapy_app
DB_PASSWORD=dev-only-password
DELTA_LAKE_PATH=./data/delta_lake
LOG_LEVEL=INFO
```

A shared stage host or the streaming profile. `ENV=staging` is a label for
humans and the audit log. It does not select a file, and it is not
`production`, so wipes still use the non-production confirmation.

```bash
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<secret>
DB_HOST=postgres
DB_PORT=5432
DB_NAME=scraping_pipeline
DB_USER=scrapy_app
DB_PASSWORD=<secret>
DELTA_LAKE_PATH=/data/delta
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
LOG_LEVEL=INFO
ENV=staging
```

Production. Same `config.yml` as stage. Stricter transport and the
destructive-op gate. Helm already injects the Redis, Postgres, and Kafka
hosts plus `ENV=production` and `KAFKA_REQUIRE_IDEMPOTENCE=true`; do not
also mount a `config/prod.yml`.

```bash
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<secret>
DB_HOST=postgres
DB_PORT=5432
DB_NAME=scraping_pipeline
DB_USER=scrapy_app
DB_PASSWORD=<secret>
DELTA_LAKE_PATH=/data/delta
KAFKA_BOOTSTRAP_SERVERS=<broker-list>
KAFKA_SECURITY_PROTOCOL=SASL_SSL
KAFKA_SASL_MECHANISM=SCRAM-SHA-512
KAFKA_SASL_USERNAME=<secret>
KAFKA_SASL_PASSWORD=<secret>
KAFKA_REQUIRE_IDEMPOTENCE=true
LOG_LEVEL=INFO
ENV=production
```

Thresholds (stage batch sizes, sitemap caps, allowed domains, topic names)
stay in `config.yml` and are identical in all three. Override a single
deployed knob with its variable when one exists, for example
`STAGE2_BATCH_SIZE` or `LOG_LEVEL`, instead of forking the file.
