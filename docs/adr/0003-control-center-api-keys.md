# ADR-0003: Control Center API keys are stored as hashes

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #658

## Context

[#189](https://github.com/BenjaminSRussell/Scrapy/issues/189) put an optional shared
secret (`CC_AUTH_TOKEN` / `CC_BASIC_AUTH`) in front of the Control Center. One
secret cannot be rotated for a single operator, which is not enough before
control routes such as start and stop exist.
[#658](https://github.com/BenjaminSRussell/Scrapy/issues/658) asks for hashed
keys in Postgres or Redis, middleware on those routes, and a mint/revoke CLI.

The Postgres role in `docker/init-db.sql` may only `SELECT` and `INSERT`
(#538). Redis is reached with `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`,
and `REDIS_DB` (#511).

## Decision

We will issue random `cc_` bearer keys and store only the SHA-256 hex digest.
The default backend is Redis. `CC_API_KEY_BACKEND=postgres` or `both` also
writes `cc_api_keys`. Revocation in Postgres is an insert into
`cc_api_key_revocations`, not an update. `/api/control/*` answers 401 unless
every configured store has a live hash. The CLI prints the secret once on
stdout. Loggers under `dashboard.cc_auth` and `dashboard.serve` scrub
key-shaped strings. `POST /api/control/start` and `stop` stay unauthorized
without a key and, once authorized, return 501 until a runner is configured.
They do not start a crawl.

## Consequences

- One operator key can be revoked without changing the dashboard shared secret.
- A Redis flush of `cc:api_key:*` invalidates keys unless Postgres still holds
  the hashes (`CC_API_KEY_BACKEND=both`).
- Existing databases need `docker/init-db.sql` re-run as the superuser before
  the Postgres backend works. The app role cannot create the tables.
- Revisit this if control actions move behind the authenticating proxy from
  #189 and these keys are no longer the control-plane gate.

## Alternatives considered

- **Shared secret only:** cannot revoke one caller without rotating everyone.
- **Store the raw key:** a Redis or Postgres dump would be a credential dump.
- **UPDATE a revoked column:** the app role is not granted UPDATE.
