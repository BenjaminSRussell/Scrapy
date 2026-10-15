"""LiveFeed: the pipeline as its Delta tables show it."""
from __future__ import annotations

import bisect
import threading
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc

from .delta import TableWatcher, WatchResult
from .queries import ExploreTree, build_journey, build_stage_detail
from .stages import (ERROR_COLUMNS, ERRORS_TABLE, STAGE_BY_ID, STAGES, TS_COLUMN, PollResult,
                     StageState, make_error_event, make_event, parse_ts)

EVENTS_PER_POLL = 500
REPLAY_EVENTS = 50
EXPLORE_REBUILD_EVERY = 30.0

_DETAIL_COLUMNS = {
    "seeds": ("url",),
    "discovery": ("url", "domain", "depth", "status_code", "requires_js", "content_size"),
    "analysis": ("is_massive_doc", "word_count", "has_quality_content"),
    "summaries": ("model",),
    "large_docs": ("compression_ratio", "original_word_count"),
}


class LiveFeed:
    mode = "live"

    def __init__(self, base_path, clock=time.time):
        self.base = Path(base_path)
        self.clock = clock
        self.watchers = {s.id: TableWatcher(self.base / s.table, s.columns) for s in STAGES}
        self.errors = TableWatcher(self.base / ERRORS_TABLE, ERROR_COLUMNS)
        self._seq = 0
        self._replayed = False
        self._last: dict[str, StageState] = {}
        self._cache: dict[tuple, object] = {}
        self._explore: tuple[int | None, float, ExploreTree] | None = None
        self._lock = threading.Lock()

    # ---- polling ----
    def poll(self, now: float) -> PollResult:
        stages: dict[str, StageState] = {}
        events: list[dict] = []
        err = _safe_poll(self.errors)
        for stage in STAGES:
            r = _safe_poll(self.watchers[stage.id])
            stages[stage.id] = StageState(status=r.status, count=r.count, last_write=r.last_write,
                                          version=r.version, error=r.error)
            for row in r.new_rows:
                self._seq += 1
                events.append(make_event(stage, row, self._seq, row["_t"]))
        if err.status == "ok":
            stages["discovery"].errors = err.count
            for row in err.new_rows:
                self._seq += 1
                events.append(make_error_event(row, self._seq, row["_t"]))
        if not self._replayed:
            self._replayed = True
            events.extend(self._replay())
        events.sort(key=lambda e: e["t"])
        self._last = stages
        return PollResult(stages=stages, events=events[-EVENTS_PER_POLL:])

    def _replay(self) -> list[dict]:
        """The newest rows already on disk at startup, so lists are not empty. Never animated."""
        rows = [(stage, row) for stage in STAGES for row in self.watchers[stage.id].recent_rows]
        rows.sort(key=lambda sr: sr[1]["_t"])
        out = []
        for stage, row in rows[-REPLAY_EVENTS:]:
            self._seq += 1
            out.append({**make_event(stage, row, self._seq, row["_t"]), "replay": True})
        return out

    def backfill(self, now: float, seconds: int, bucket: int) -> dict[str, list[tuple[float, int]]]:
        """Cumulative count at each bucket end over the last ``seconds``, from ``_ingestion_time``."""
        start = now - seconds
        ends = [start + bucket * (i + 1) for i in range(seconds // bucket)]
        out = {}
        for stage in STAGES:
            w = self.watchers[stage.id]
            if w.version is None:
                w.poll()
            try:
                table = w.table([TS_COLUMN])
            except Exception:
                continue
            if table is None or TS_COLUMN not in table.column_names:
                continue
            epochs = _epochs(table.column(TS_COLUMN))
            recent = sorted(t for t in epochs if t is not None and t > start)
            before = table.num_rows - len(recent)
            out[stage.id] = [(end, before + bisect.bisect_right(recent, end)) for end in ends]
        return out

    def services(self, now: float) -> list[dict]:
        states = self._last or {s.id: StageState() for s in STAGES}
        found = sum(1 for s in states.values() if s.status == "ok")
        errored = [sid for sid, s in states.items() if s.status == "error"]
        newest = max((s.last_write for s in states.values() if s.last_write), default=None)
        if not self.base.is_dir():
            status, detail = "down", f"No lake at {self.base}"
        elif errored:
            status, detail = "warn", f"{found}/{len(STAGES)} tables · can't read {', '.join(errored)}"
        elif found == 0:
            status, detail = "warn", f"0/{len(STAGES)} tables"
        else:
            status, detail = "ok", f"{found}/{len(STAGES)} tables"
        return [{"id": "delta", "label": "Delta Lake", "status": status, "detail": detail, "checked_at": now,
                 "data": {"path": str(self.base), "tables_found": found, "tables_total": len(STAGES),
                          "last_write": newest}}]

    # ---- queries ----
    def _cached(self, key: tuple, build):
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        value = build()
        with self._lock:
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = value
        return value

    def stage_detail(self, stage_id: str) -> dict | None:
        if stage_id not in STAGE_BY_ID:
            return None
        w = self.watchers[stage_id]

        def build():
            table = w.table(_DETAIL_COLUMNS[stage_id])
            rows = table.to_pylist() if table is not None else []
            return build_stage_detail(stage_id, rows, [])

        detail = dict(self._cached(("detail", stage_id, w.version), build))
        detail["recent"] = build_stage_detail(stage_id, [], w.recent_rows)["recent"]
        return detail

    def url_journey(self, url: str) -> dict | None:
        found: dict[str, dict | None] = {}
        for stage in STAGES:
            table = self.watchers[stage.id].table(stage.columns, url=url)
            rows = table.to_pylist() if table is not None else []
            for row in rows:
                row["_t"] = parse_ts(row.get(TS_COLUMN))
            rows.sort(key=lambda r: r["_t"] or 0, reverse=True)
            found[stage.id] = rows[0] if rows else None
        if not any(found.values()):
            return None
        return build_journey(url, found)

    def explore(self, path: list[str]) -> dict | None:
        w = self.watchers["discovery"]
        now = self.clock()
        current = self._explore
        if current is None or (current[0] != w.version and now - current[1] >= EXPLORE_REBUILD_EVERY):
            table = w.table(["url", "depth"])
            if table is None:
                return None
            depth = table.column("depth").to_pylist() if "depth" in table.column_names else [None] * table.num_rows
            tree = ExploreTree(zip(table.column("url").to_pylist(), depth))
            current = self._explore = (w.version, now, tree)
        return current[2].node(path)


def _safe_poll(watcher: TableWatcher) -> WatchResult:
    """One table's failure is that table's status, never the whole poll's."""
    try:
        return watcher.poll()
    except Exception as e:
        return WatchResult("error", watcher._count, watcher.version, watcher._watermark,
                           error=f"{type(e).__name__}: {e}")


def _epochs(column: pa.ChunkedArray) -> list[float | None]:
    """ISO strings (any of the on-disk forms) to epoch seconds, UTC, vectorised."""
    if pa.types.is_timestamp(column.type):
        return [v.timestamp() if v else None for v in column.to_pylist()]
    head = pc.utf8_slice_codeunits(column.cast(pa.string()), 0, 19)
    parsed = pc.strptime(head, format="%Y-%m-%dT%H:%M:%S", unit="s", error_is_null=True)
    return [None if v is None else v / 1 for v in pc.cast(parsed, pa.int64()).to_pylist()]
