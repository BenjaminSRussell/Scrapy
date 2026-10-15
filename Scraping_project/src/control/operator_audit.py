"""Append-only audit of Control Center operator actions (#657).

Each event is ``actor``, ``action``, ``ts`` (UTC ISO-8601), and ``job_id``.
The JSONL file is the record the dashboard reads. The same row can be
mirrored to a Delta table (``CC_AUDIT_DELTA_PATH``). There is no update
or delete. Executing start/stop/pause stays with #148; this module only
records the call.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from src.core.timeutil import utc_now_iso

logger = logging.getLogger(__name__)

AUDIT_EVENT_FIELDS: tuple[str, ...] = ("actor", "action", "ts", "job_id")
DEFAULT_AUDIT_LIMIT = 50
MAX_AUDIT_LIMIT = 200

_VERBS = ("start", "stop", "pause", "resume")
_TARGETS = ("", "_stage1", "_stage2", "_stage3", "_stage4")
CONTROL_ACTIONS = frozenset(verb + target for verb in _VERBS for target in _TARGETS)

_ACTOR_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

# Draft 2020-12 document for the on-disk object. ``ts`` is text, not a
# number, so the JSONL line and the Delta column are the same value.
AUDIT_EVENT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OperatorAuditEvent",
    "type": "object",
    "additionalProperties": False,
    "required": list(AUDIT_EVENT_FIELDS),
    "properties": {
        "actor": {"type": "string", "minLength": 1, "maxLength": 128},
        "action": {"type": "string", "enum": sorted(CONTROL_ACTIONS)},
        "ts": {"type": "string", "pattern": _TS_RE.pattern},
        "job_id": {"type": "string", "maxLength": 128},
    },
}


class AuditError(ValueError):
    """The event was rejected and was not written."""


def _no_controls(value: str, field: str) -> None:
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise AuditError(f"{field} has control characters")


def normalize_event(
    *,
    actor: str,
    action: str,
    job_id: str = "",
    ts: str | None = None,
) -> dict[str, str]:
    """Return a schema-valid event. Does not touch the log."""
    if not isinstance(actor, str) or not isinstance(action, str) or not isinstance(job_id, str):
        raise AuditError("actor, action, and job_id must be strings")
    actor = actor.strip()
    if not _ACTOR_RE.fullmatch(actor):
        raise AuditError("actor must be 1-128 of [A-Za-z0-9_.:@-]")
    if action not in CONTROL_ACTIONS:
        raise AuditError(f"unknown action: {action!r}")
    if len(job_id) > 128:
        raise AuditError("job_id too long")
    _no_controls(job_id, "job_id")
    stamp = utc_now_iso() if ts is None else ts
    if not isinstance(stamp, str) or not _TS_RE.fullmatch(stamp):
        raise AuditError("ts must be UTC ISO-8601 with a Z suffix")
    return {"actor": actor, "action": action, "ts": stamp, "job_id": job_id}


def validate_stored(raw: dict[str, Any]) -> dict[str, str]:
    """Accept a previously written line, including an action retired later."""
    if not isinstance(raw, dict):
        raise AuditError("event must be an object")
    out: dict[str, str] = {}
    for key in AUDIT_EVENT_FIELDS:
        value = raw.get(key)
        if not isinstance(value, str):
            raise AuditError(f"{key} must be a string")
        out[key] = value
    _no_controls(out["actor"], "actor")
    _no_controls(out["action"], "action")
    _no_controls(out["job_id"], "job_id")
    _no_controls(out["ts"], "ts")
    return out


class OperatorAuditLog:
    """JSONL log opened only for append. Reads never rewrite the file."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(
        self,
        *,
        actor: str,
        action: str,
        job_id: str = "",
        ts: str | None = None,
    ) -> dict[str, str]:
        event = normalize_event(actor=actor, action=action, job_id=job_id, ts=ts)
        line = json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        logger.info("operator audit %s actor=%s job_id=%s", action, actor, job_id or "-")
        return event

    def read_last(self, limit: int = DEFAULT_AUDIT_LIMIT) -> list[dict[str, str]]:
        """Newest first. A torn or non-object line is skipped, not deleted."""
        if limit < 0:
            raise AuditError("limit must be >= 0")
        if not self.path.exists():
            return []
        with self._lock:
            text = self.path.read_text(encoding="utf-8")
        rows: list[dict[str, str]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                rows.append(validate_stored(raw))
            except AuditError:
                continue
        if limit == 0:
            return []
        return list(reversed(rows[-limit:]))


def append_delta(event: dict[str, str], table_path: Path | str) -> None:
    """Append one event to a Delta table with ``OPERATOR_AUDIT_SCHEMA``.

    The first call creates the table. Later calls append. This never
    overwrites.
    """
    import pyarrow as pa
    from deltalake import write_deltalake

    from src.core.schemas import OPERATOR_AUDIT_SCHEMA

    row = {name: str(event[name]) for name in OPERATOR_AUDIT_SCHEMA.names}
    table = pa.Table.from_pylist([row], schema=OPERATOR_AUDIT_SCHEMA)
    path = Path(table_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if (path / "_delta_log").is_dir():
        write_deltalake(str(path), table, mode="append")
    else:
        write_deltalake(str(path), table, mode="error")


def mirror_delta(event: dict[str, str], table_path: Path | str) -> bool:
    """Best-effort Delta copy. The JSONL line is already durable."""
    try:
        append_delta(event, table_path)
    except Exception:
        logger.exception("delta audit append failed")
        return False
    return True
