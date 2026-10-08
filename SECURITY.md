# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/BenjaminSRussell/Scrapy/security/advisories/new)
(repository **Security** tab → **Report a vulnerability**). Only the maintainer
([@BenjaminSRussell](https://github.com/BenjaminSRussell)) can see the report.

If that button is not available, open an issue titled **"Security contact request"**
that contains **no details** of the problem. The maintainer will reply with a private
channel. Send the details there.

Please include:

- what is affected (file, endpoint, workflow, image, or Helm chart) and the commit or tag;
- steps to reproduce or a proof of concept;
- the impact you expect (data exposure, SSRF, code execution, denial of service, ...).

What to expect:

| Step | Target |
|------|--------|
| Acknowledgement | within 3 business days |
| Initial assessment (accepted / needs info / not a vulnerability) | within 10 business days |
| Fix or mitigation for accepted reports | as soon as practical; tracked in a private advisory |

You will be credited in the advisory and the [CHANGELOG](CHANGELOG.md) unless you ask not to be.

## Supported versions

The project has not cut a stable release yet (`pyproject.toml` version `0.1.0`).
Security fixes land on `main` only; release images are rebuilt from `v*` tags
(see [docs/RELEASING.md](docs/RELEASING.md)).

| Version | Supported |
|---------|-----------|
| `main` | yes |
| any earlier commit or tag | no, upgrade to `main` |

## Scope

In scope: code under `Scraping_project/`, the container images built by
`.github/workflows/cd-release.yml`, the Helm chart and Kubernetes manifests under
`Scraping_project/k8s/`, and the GitHub workflows.

Out of scope: `temp_scripts/` (unsupported one-off experiments), third-party
services (Kafka, Redis, Postgres, Grafana) beyond how this project configures them,
and findings that need an already-compromised host or cluster-admin access.

## Handling secrets

These rules apply to contributors and operators alike (#482).

- **Never commit real credentials.** Secrets belong in `Scraping_project/.env` (or a
  Kubernetes Secret / external secret store). The repo-root `.gitignore` ignores `.env` and
  `.env.*`; only the `*.env.example` templates are tracked, and they hold placeholders.
- **Templates:** [`Scraping_project/.env.example`](Scraping_project/.env.example) (pipeline,
  Compose) and
  [`Scraping_project/kafka-delta-ingest/.env.example`](Scraping_project/kafka-delta-ingest/.env.example)
  list every variable and mark required vs optional.
- **No secrets in `config.yml`, Helm `values*.yaml`, workflow files, logs or issues.**
  Pass them as environment variables (`DB_PASSWORD`, `REDIS_PASSWORD`,
  `KAFKA_SASL_PASSWORD`, `GRAFANA_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`) or Helm
  `secrets.*` / existing Secret references. Log redaction (`src/log_redaction.py`) is a
  safety net, not permission to log secrets.
- **Local-dev defaults are public.** `docker-compose.yml` falls back to well-known
  passwords when `.env` does not set them: Postgres superuser `postgres`
  (`POSTGRES_PASSWORD`), app role `scrapy_app_dev` (`APP_DB_PASSWORD`), Grafana `admin`
  (`GRAFANA_ADMIN_PASSWORD`), and Redis without AUTH (`REDIS_PASSWORD`). Set real values
  before the stack is reachable from anything but your own machine.

### Rotating a leaked or default password

If a credential was committed, pasted in an issue/log, or a default above was ever used on a
shared host, treat it as compromised:

1. **Rotate first.** Put a new value in `.env` (or the Secret), then apply it:
   - Postgres: `docker compose exec postgres psql -U postgres -c "ALTER ROLE postgres PASSWORD '<new>'"`
     (and `ALTER ROLE scrapy_app PASSWORD '<new>'` for `APP_DB_PASSWORD`), then
     `docker compose up -d --force-recreate` so the exporter, Grafana and workers pick it up.
     `POSTGRES_PASSWORD` in `.env` only seeds a *new* volume; an existing database keeps its
     old password until you run `ALTER ROLE`.
   - Grafana: `docker compose exec grafana grafana cli admin reset-admin-password '<new>'`.
   - Redis: set `REDIS_PASSWORD`, then `docker compose up -d --force-recreate`.
   - Kafka SASL / cloud keys: rotate at the provider, then update the Secret.
2. **Then clean up.** Removing the commit does not un-leak it: forks, clones and caches keep
   it. Rewrite history only after rotating (`git filter-repo`), and ask GitHub support to
   purge cached views if needed.
3. **Report it** privately as described above if the leak affects anyone besides you.
