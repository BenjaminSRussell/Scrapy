# ADR-0004: Control Center operator actions are an append-only audit log

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #657

## Context

Start/stop of a stage (#148) does not exist yet, and nothing records who asked
for it. In a multi-operator setting that missing line is the problem: a pause
of Stage 2 has to name a person, an action, a time, and a job. The Control
Center today is a read-only dashboard (`/api/metrics`, `/api/health`,
`/api/queues`). Seed changes and destructive wipes already keep their own
JSONL audits; those logs are not the control-plane record.

## Decision

We will record every Control Center control call as one append-only event with
exactly four fields: `actor`, `action`, `ts` (UTC ISO-8601 text with a `Z`
suffix), and `job_id` (empty when the call is not job-scoped).

- The source of truth is a JSONL file (`CC_AUDIT_LOG`, default
  `Scraping_project/data/operator_audit/events.jsonl`). The file is only
  opened for append. Reads skip a bad line and do not rewrite the file.
- The same row may be mirrored to a Delta table at `CC_AUDIT_DELTA_PATH`
  using `OPERATOR_AUDIT_SCHEMA` (`src/core/schemas.py`). The mirror is
  append-only. It is off unless that path is set, so the pipeline lake is
  not involved.
- `POST /api/control` writes the event. It does not start, stop, or pause
  workers; #148 will perform the action and must keep this write.
- `GET /api/audit` and the Activity tab panel list the last N events. Both
  are read-only. The panel has no clear control.
- Allowed actions are `start`, `stop`, `pause`, and `resume`, optionally
  suffixed with `_stage1` … `_stage4` (for example `pause_stage2`).
- When HTTP Basic auth is on, `actor` is the authenticated user. Otherwise
  the server uses `X-Control-Actor`, then the JSON `actor`, then `operator`.
  The client does not choose `ts`.

## Consequences

A later control implementation has one place to record the call, and the
dashboard can show who did it without a new table in the crawl lake. Adding
an action or a field is a schema change, not a silent extra key. Delta stays
optional so the dashboard process does not need a lake. Operators must not
point `CC_AUDIT_DELTA_PATH` at an existing crawl table; the schema is not
one of those tables. Replaying or deleting a line is out of band on purpose.

## Alternatives considered

- **Write only to a Delta table in the crawl lake.** The dashboard would
  depend on Delta for a log that has to work when the lake is down, and it
  would add a declared pipeline table for an ops record.
- **Reuse the in-browser activity feed.** That feed is session-local,
  clearable, and not tied to an actor. It is the wrong store for
  accountability.
- **Client-supplied timestamps.** A caller could backdate a pause. The
  server clock is the record.
