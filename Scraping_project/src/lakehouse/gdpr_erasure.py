"""Delete stage-table rows by url_hash and tombstone them (#688).

Operational stub. Legal review is still required before treating a hash as
erased under GDPR or university policy. ``LakehouseManager.write`` and
``merge_into`` drop rows whose ``url_hash`` is still inside the tombstone TTL.
A missing tombstone table blocks nothing. A tombstone read error fails open
so a bad log cannot halt every write. Direct Delta commits that bypass
``LakehouseManager`` are not covered.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LEGAL_NOTE = (
    "Legal review is still required. This is an operational stub that deletes "
    "matching url_hash rows and blocks re-ingest until the tombstone TTL. "
    "It is not a determination that erasure satisfies GDPR or university policy."
)

TOMBSTONE_TABLE = "gdpr_tombstones"
AUDIT_NAME = "gdpr_erasure.jsonl"
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
_HASH_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_FAR_FUTURE = datetime(9999, 1, 1, tzinfo=UTC)

# Stage and seed tables that can carry a page identity. Tables without a
# url_hash column, or without a Delta log, are skipped.
STAGE_TABLES: tuple[str, ...] = (
    "seed_urls",
    "uconn_urls",
    "stage1_discovery",
    "stage1_errors",
    "stage1_offsite_candidates",
    "js_spider_queue",
    "stage2_queue",
    "stage2_page_analysis",
    "stage2_errors",
    "stage3_analytics",
    "stage3_summaries",
    "stage4_large_docs",
    "stage4_large_doc_summaries",
    "stage4_summaries",
    "metadata_queue",
)

# Quarantine and the tombstone table itself must remain writable.
_EXEMPT = frozenset({TOMBSTONE_TABLE, "cast_quarantine", "domain_quarantine"})

_index_lock = threading.Lock()
# base path -> (delta-log mtime_ns, url_hash -> expires_at)
_index: dict[str, tuple[int, dict[str, datetime]]] = {}


def validate_url_hashes(url_hashes: list[str]) -> list[str]:
    """Deduped hashes safe to interpolate into a Delta predicate."""
    out: list[str] = []
    for raw in url_hashes:
        digest = str(raw).strip()
        if not _HASH_RE.fullmatch(digest):
            raise ValueError(
                "url_hash must be 1-128 characters of [A-Za-z0-9_-] "
                f"(got {digest!r}). Pass the stored hash, not a URL."
            )
        if digest not in out:
            out.append(digest)
    if not out:
        raise ValueError("at least one url_hash is required")
    return out


def _predicate(url_hashes: list[str]) -> str:
    quoted = ", ".join(f"'{h}'" for h in url_hashes)
    if len(url_hashes) == 1:
        return f"url_hash = '{url_hashes[0]}'"
    return f"url_hash IN ({quoted})"


def _parse_expires(value: Any) -> datetime:
    """Parse an ISO expiry. Unparseable stamps stay blocking (fail closed)."""
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt
    if not isinstance(value, str) or not value.strip():
        return _FAR_FUTURE
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("[GDPR] unparseable tombstone expires_at %r; treating it as still active", value)
        return _FAR_FUTURE
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def invalidate_tombstone_cache(base_path: Path | str) -> None:
    with _index_lock:
        _index.pop(str(Path(base_path)), None)


def _load_index(base_path: Path) -> dict[str, datetime]:
    log = base_path / TOMBSTONE_TABLE / "_delta_log"
    key = str(base_path)
    if not log.exists():
        return {}
    try:
        mtime = log.stat().st_mtime_ns
    except OSError:
        mtime = -1
    with _index_lock:
        cached = _index.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    try:
        from deltalake import DeltaTable

        table = DeltaTable(str(base_path / TOMBSTONE_TABLE))
        arrow = table.to_pyarrow_table(columns=["url_hash", "expires_at"])
    except Exception as exc:
        logger.error("[GDPR] tombstone read failed open (%s); re-ingest is not blocked", exc)
        return {}
    found: dict[str, datetime] = {}
    for row in arrow.to_pylist():
        digest = row.get("url_hash")
        if not isinstance(digest, str) or not digest:
            continue
        expires = _parse_expires(row.get("expires_at"))
        previous = found.get(digest)
        if previous is None or expires > previous:
            found[digest] = expires
    with _index_lock:
        _index[key] = (mtime, found)
    return found


def active_hashes(base_path: Path | str, now: datetime | None = None) -> set[str]:
    """url_hash values whose tombstone expiry is still strictly in the future."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    index = _load_index(Path(base_path))
    return {digest for digest, expires in index.items() if expires > moment}


def suppress_reingest(base_path: Path | str, table_name: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop rows whose url_hash is tombstoned. Same list when nothing is dropped."""
    if not rows or table_name in _EXEMPT:
        return rows
    blocked = active_hashes(base_path)
    if not blocked:
        return rows
    kept: list[dict[str, Any]] = []
    dropped = 0
    for row in rows:
        digest = row.get("url_hash")
        if isinstance(digest, str) and digest in blocked:
            dropped += 1
            continue
        kept.append(row)
    if not dropped:
        return rows
    logger.warning(
        "[GDPR] suppressed %s re-ingest row(s) for %s; tombstone TTL still active. %s",
        dropped,
        table_name,
        LEGAL_NOTE,
    )
    return kept


def _table_path(manager: Any, table_name: str) -> Path:
    tables = getattr(manager, "tables", None) or {}
    path = tables.get(table_name)
    if path is None:
        path = Path(manager.base_path) / table_name
    return Path(path)


def _column_names(dt: Any) -> list[str]:
    try:
        import pyarrow as pa

        return list(pa.schema(dt.schema().to_arrow()).names)
    except Exception:
        return [field.name for field in dt.schema().fields]


def _match_count(dt: Any, url_hashes: list[str]) -> int:
    wanted = set(url_hashes)
    column = dt.to_pyarrow_table(columns=["url_hash"])["url_hash"].to_pylist()
    return sum(1 for value in column if value in wanted)


def _scan_table(manager: Any, table_name: str, url_hashes: list[str]) -> tuple[int | None, str | None]:
    """(match count, skip reason). A count of None means the table was skipped."""
    path = _table_path(manager, table_name)
    if not (path / "_delta_log").exists():
        return None, "table does not exist"
    try:
        from deltalake import DeltaTable

        dt = DeltaTable(str(path))
        if "url_hash" not in _column_names(dt):
            return None, "no url_hash column"
        return _match_count(dt, url_hashes), None
    except Exception as exc:
        return None, str(exc)


def _delete_matches(manager: Any, table_name: str, url_hashes: list[str]) -> int:
    from deltalake import DeltaTable

    path = _table_path(manager, table_name)
    predicate = _predicate(url_hashes)
    locker = getattr(manager, "_table_lock", None)
    if locker is None:
        metrics = DeltaTable(str(path)).delete(predicate)
    else:
        with locker(table_name):
            metrics = DeltaTable(str(path)).delete(predicate)
    if isinstance(metrics, dict) and "num_deleted_rows" in metrics:
        return int(metrics.get("num_deleted_rows") or 0)
    return -1


def _write_tombstones(
    base: Path,
    url_hashes: list[str],
    *,
    erased_at: str,
    expires_at: str,
    ttl_seconds: int,
    actor: str,
    reason: str,
) -> int:
    import pyarrow as pa
    from deltalake import write_deltalake

    path = base / TOMBSTONE_TABLE
    path.mkdir(parents=True, exist_ok=True)
    n = len(url_hashes)
    table = pa.table(
        {
            "url_hash": url_hashes,
            "erased_at": [erased_at] * n,
            "expires_at": [expires_at] * n,
            "ttl_seconds": [str(ttl_seconds)] * n,
            "actor": [actor] * n,
            "reason": [reason] * n,
            "legal_review_required": ["true"] * n,
        }
    )
    kwargs: dict[str, Any] = {"mode": "append"}
    if (path / "_delta_log").exists():
        kwargs["schema_mode"] = "merge"
    write_deltalake(str(path), table, **kwargs)
    invalidate_tombstone_cache(base)
    return n


def _audit(base: Path, record: dict[str, Any]) -> str:
    audit = base / "_audit" / AUDIT_NAME
    audit.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True) + "\n"
    with _index_lock:
        with open(audit, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    return str(audit)


def erase_url_hashes(
    manager: Any,
    url_hashes: list[str],
    *,
    execute: bool = False,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    actor: str = "",
    reason: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dry-run (default) or delete matching rows, then tombstone and audit.

    A hash that is not currently stored is still tombstoned on execute, so a
    later ingest of that exact ``url_hash`` is refused until ``expires_at``.
    Re-running keeps the later expiry: a hash stays blocked while any
    tombstone row for it is unexpired.
    """
    hashes = validate_url_hashes(list(url_hashes))
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be > 0")
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    expires = moment + timedelta(seconds=int(ttl_seconds))
    actor = (actor or "operator").strip()[:500] or "operator"
    reason = (reason or "operator erasure request").strip()[:500] or "operator erasure request"
    erased_at = moment.isoformat(timespec="seconds")
    expires_at = expires.isoformat(timespec="seconds")

    matched: dict[str, int] = {}
    skipped: dict[str, str] = {}
    for table_name in STAGE_TABLES:
        count, skip = _scan_table(manager, table_name, hashes)
        if skip is not None:
            skipped[table_name] = skip
        elif count:
            matched[table_name] = count

    result: dict[str, Any] = {
        "dry_run": not execute,
        "url_hashes": hashes,
        "ttl_seconds": int(ttl_seconds),
        "erased_at": erased_at,
        "expires_at": expires_at,
        "actor": actor,
        "reason": reason,
        "matched": matched,
        "skipped": skipped,
        "deleted": {},
        "tombstones_written": 0,
        "audit_path": None,
        "errors": {},
        "legal_review_required": True,
        "note": LEGAL_NOTE,
    }
    if not execute:
        return result

    deleted: dict[str, int] = {}
    errors: dict[str, str] = {}
    for table_name, count in matched.items():
        try:
            removed = _delete_matches(manager, table_name, hashes)
            deleted[table_name] = count if removed < 0 else removed
        except Exception as exc:
            errors[table_name] = str(exc)
            logger.error("[GDPR] delete failed for %s: %s", table_name, exc)
    result["deleted"] = deleted
    result["errors"] = errors

    base = Path(manager.base_path)
    try:
        result["tombstones_written"] = _write_tombstones(
            base,
            hashes,
            erased_at=erased_at,
            expires_at=expires_at,
            ttl_seconds=int(ttl_seconds),
            actor=actor,
            reason=reason,
        )
    except Exception as exc:
        errors["gdpr_tombstones"] = str(exc)
        result["errors"] = errors
        logger.error("[GDPR] tombstone write failed: %s", exc)

    record = {
        "at": erased_at,
        "action": "erase",
        "url_hashes": hashes,
        "ttl_seconds": int(ttl_seconds),
        "expires_at": expires_at,
        "actor": actor,
        "reason": reason,
        "deleted": deleted,
        "errors": errors,
        "legal_review_required": True,
        "note": LEGAL_NOTE,
    }
    try:
        result["audit_path"] = _audit(base, record)
    except Exception as exc:
        errors["audit"] = str(exc)
        result["errors"] = errors
        logger.error("[GDPR] audit write failed: %s", exc)
    return result
