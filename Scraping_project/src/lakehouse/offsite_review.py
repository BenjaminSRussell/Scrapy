"""Promotion state and retention for stage1_offsite_candidates (#878).

The Scout appends one row per (source page, external URL) sighting. Rows
written before a review have no ``status`` column/value; a missing status
means ``pending``. Review adds ``status`` (pending|accepted|rejected),
``reviewed_at`` and, after promotion, ``promoted_at`` to every row of the URL.

* ``set_status(urls, "accepted"|"rejected")``: the review decision.
* ``promote_accepted()``: accepted, not yet promoted URLs go to the seed table
  through SeedManager (an idempotent MERGE on url_hash), then are stamped
  ``promoted_at``. Running it twice adds nothing.
* ``gc()``: deletes pending sightings older than
  ``delta_lake.offsite_pending_retention_days`` (30) and rejected ones reviewed
  more than ``delta_lake.offsite_rejected_retention_days`` (7) ago. Accepted
  rows are kept as the promotion audit trail.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

TABLE = "stage1_offsite_candidates"
STATUSES = ("pending", "accepted", "rejected")
STATE_COLUMNS = ("status", "reviewed_at", "promoted_at")

try:
    from prometheus_client import Gauge as _Gauge

    OFFSITE_CANDIDATE_URLS = _Gauge(
        "delta_offsite_candidate_urls", "Distinct offsite candidate URLs by review status (#878).", ["status"]
    )
except Exception:  # prometheus_client missing or already registered
    OFFSITE_CANDIDATE_URLS = None


def _sql_str(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class OffsiteReview:
    def __init__(self, manager: Any, config: Any = None):
        self.manager = manager
        if config is None:
            try:
                from src.core.config import Config

                config = Config.get_instance()
            except Exception:
                config = None
        get = (lambda k, d: config.get(k, d)) if config is not None else (lambda k, d: d)
        self.pending_days = float(get("delta_lake.offsite_pending_retention_days", 30) or 0)
        self.rejected_days = float(get("delta_lake.offsite_rejected_retention_days", 7) or 0)

    # -- helpers -----------------------------------------------------------
    @property
    def path(self):
        return self.manager.tables.get(TABLE, self.manager.base_path / TABLE)

    def _table(self):
        from deltalake import DeltaTable

        if not (self.path / "_delta_log").exists():
            return None
        return DeltaTable(str(self.path))

    def _ensure_state_columns(self, dt) -> None:
        import pyarrow as pa
        from deltalake import Field
        from deltalake.schema import PrimitiveType

        names = set(pa.schema(dt.schema().to_arrow()).names)
        missing = [c for c in STATE_COLUMNS if c not in names]
        if missing:
            dt.alter.add_columns([Field(c, PrimitiveType("string"), nullable=True) for c in missing])

    def _rows(self) -> list[dict[str, Any]]:
        dt = self._table()
        return [] if dt is None else dt.to_pyarrow_table().to_pylist()

    # -- read --------------------------------------------------------------
    def summary(self) -> list[dict[str, Any]]:
        """One entry per external URL: status, sightings, first/last seen, promoted_at."""
        by_url: dict[str, dict[str, Any]] = {}
        for r in self._rows():
            url = r.get("external_url")
            if not url:
                continue
            e = by_url.setdefault(
                url,
                {"external_url": url, "status": "pending", "sightings": 0, "first_seen": None,
                 "last_seen": None, "reviewed_at": None, "promoted_at": None},
            )
            e["sightings"] += 1
            seen = r.get("discovered_at")
            if seen:
                e["first_seen"] = min(filter(None, [e["first_seen"], seen]))
                e["last_seen"] = max(filter(None, [e["last_seen"], seen]))
            if r.get("status"):
                e["status"] = r["status"]
            for k in ("reviewed_at", "promoted_at"):
                if r.get(k):
                    e[k] = r[k]
        return sorted(by_url.values(), key=lambda e: (e["status"], e["external_url"]))

    def counts(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for e in self.summary():
            out[e["status"]] = out.get(e["status"], 0) + 1
        if OFFSITE_CANDIDATE_URLS is not None:
            for status, n in out.items():
                OFFSITE_CANDIDATE_URLS.labels(status=status).set(n)
        return out

    # -- write -------------------------------------------------------------
    def set_status(self, urls: Iterable[str], status: str, now: datetime | None = None) -> int:
        """Record a review decision for every sighting of ``urls``. Returns rows updated."""
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        urls = sorted({u for u in urls if u})
        dt = self._table()
        if dt is None or not urls:
            return 0
        stamp = (now or datetime.now()).isoformat()
        with self.manager._table_lock(TABLE):
            self._ensure_state_columns(dt)
            from deltalake import DeltaTable

            dt = DeltaTable(str(self.path))
            predicate = f"external_url IN ({', '.join(_sql_str(u) for u in urls)})"
            metrics = dt.update(
                updates={"status": _sql_str(status), "reviewed_at": _sql_str(stamp)}, predicate=predicate
            )
        n = int((metrics or {}).get("num_updated_rows", 0) or 0)
        logger.info(f"[monitoring] offsite_review status={status} urls={len(urls)} rows={n}")
        return n

    def promote_accepted(self, seed_manager: Any = None, now: datetime | None = None) -> list[str]:
        """Seed accepted URLs not yet promoted; stamp promoted_at. Returns the URLs promoted."""
        todo = [e["external_url"] for e in self.summary() if e["status"] == "accepted" and not e["promoted_at"]]
        if not todo:
            return []
        if seed_manager is None:
            from src.lakehouse.seed_manager import SeedManager

            seed_manager = SeedManager(self.manager)
        seed_manager.add_urls_to_seeds(todo, source_url="offsite-review", source_spider="offsite_review")
        stamp = (now or datetime.now()).isoformat()
        from deltalake import DeltaTable

        with self.manager._table_lock(TABLE):
            dt = DeltaTable(str(self.path))
            self._ensure_state_columns(dt)
            dt = DeltaTable(str(self.path))
            predicate = f"external_url IN ({', '.join(_sql_str(u) for u in todo)})"
            dt.update(updates={"promoted_at": _sql_str(stamp)}, predicate=predicate)
        logger.info(f"[monitoring] offsite_review promoted={len(todo)}")
        return todo

    def gc(self, now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
        """Delete stale pending and old rejected sightings (accepted rows are kept)."""
        result = {"pending": 0, "rejected": 0}
        dt = self._table()
        if dt is None:
            return result
        now = now or datetime.now()
        clauses = []
        import pyarrow as pa

        schema = pa.schema(dt.schema().to_arrow())
        if "discovered_at" in schema.names and not pa.types.is_string(schema.field("discovered_at").type):
            logger.warning("offsite GC skipped: discovered_at is not an ISO-8601 string column")
            return result
        rows = dt.to_pyarrow_table().to_pylist()
        has_status = "status" in schema.names
        if self.pending_days > 0:
            cutoff = (now - timedelta(days=self.pending_days)).isoformat()
            status_ok = "(status IS NULL OR status = 'pending')" if has_status else "TRUE"
            clauses.append(f"({status_ok} AND discovered_at IS NOT NULL AND discovered_at < {_sql_str(cutoff)})")
            result["pending"] = sum(
                1 for r in rows
                if (r.get("status") in (None, "pending")) and r.get("discovered_at") and r["discovered_at"] < cutoff
            )
        if self.rejected_days > 0 and has_status:
            cutoff = (now - timedelta(days=self.rejected_days)).isoformat()
            clauses.append(f"(status = 'rejected' AND reviewed_at IS NOT NULL AND reviewed_at < {_sql_str(cutoff)})")
            result["rejected"] = sum(
                1 for r in rows
                if r.get("status") == "rejected" and r.get("reviewed_at") and r["reviewed_at"] < cutoff
            )
        if dry_run or not clauses or not (result["pending"] or result["rejected"]):
            return result
        from deltalake import DeltaTable

        with self.manager._table_lock(TABLE):
            DeltaTable(str(self.path)).delete(" OR ".join(clauses))
        logger.info(f"[monitoring] offsite_gc pending={result['pending']} rejected={result['rejected']}")
        self.counts()
        return result
