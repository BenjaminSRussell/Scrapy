# ADR-0004: URL allow/block is YAML plus a Redis overlay

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #633

## Context

Stage 1 already has priority keywords in `config.yml`, but those are not a
list ops can change without a deploy. An emergency block (a leaked path, a
host that asked us to stop) has to take effect on every scout without
shipping a new image. The crawl kill switch stops the whole crawl; it is the
wrong tool for one path.

## Decision

We will keep URL allow and block rules in two layers:

1. `stage1.url_policy.allow` and `stage1.url_policy.block` in `config.yml` are
   the baseline. They ship with the process.
2. Redis sets `url_policy:block` and `url_policy:allow` are the hot overlay.
   `cli.py url-policy` adds and removes members. Scout re-reads them every
   `stage1.url_policy.reload_seconds` (default 5). A failed read keeps the
   last overlay; YAML rules still apply.

A block rule always wins. If the combined allow list is empty, the allowlist
is off and only blocks drop URLs. Scout drops a matching URL before it yields
a request or enqueues Stage 2 / the JS queue. Every successful add or remove
is appended to `url_policy:audit` (newest first, capped at 1000) with actor,
reason, and time. The Redis connection stays `REDIS_HOST` / `REDIS_PORT` /
`REDIS_PASSWORD` / `REDIS_DB`.

## Consequences

- Ops can block a path within one reload interval without a redeploy, and
  the audit list says who did it.
- Key names and the "empty allow means open" rule are now a contract. Changing
  either needs a new ADR and a migration note.
- A Redis blip does not forget the last overlay and does not ignore YAML.
  The first read, if Redis is down, is YAML only.
- Allow and block rules are not a substitute for `allowed_domains` or the
  SSRF guard. Those still run.

## Alternatives considered

- **YAML only, reloaded from disk.** A config reload is not guaranteed on
  every pod, and editing the file is a deploy. Rejected for the emergency path.
- **Kill switch.** Stops every download, not one path. Kept for abuse that
  is cluster-wide (#456).
- **Fail closed when Redis is down.** An overlay outage would halt discovery
  even with an empty block list. Rejected; YAML remains the floor.
