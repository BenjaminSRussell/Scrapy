"""Polls a feed, keeps history and recent events, and fans updates out to subscribers."""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque

from .history import History
from .stages import EDGES, STAGES, StageState, freshness

logger = logging.getLogger(__name__)

EVENTS_KEPT = 500
RECENT_IN_SNAPSHOT = 50
SUBSCRIBER_QUEUE = 64
HISTORY_SECONDS = 1800
BUCKET_SECONDS = 10
MAX_BACKOFF = 30.0


class Subscription:
    """One stream client. ``get`` returns ``(kind, payload)`` or ``None`` on timeout."""

    def __init__(self, maxsize: int = SUBSCRIBER_QUEUE):
        self.queue: queue.Queue = queue.Queue(maxsize)

    def get(self, timeout: float):
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def offer(self, kind: str, payload: dict, snapshot) -> None:
        """Queue a message; on overflow replace the backlog with a fresh snapshot."""
        try:
            self.queue.put_nowait((kind, payload))
        except queue.Full:
            while True:
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break
            self.queue.put_nowait(("snapshot", snapshot()))


class Hub:
    def __init__(self, feed, interval: float = 2.0, clock=time.time, probe=None,
                 service_every: float = 10.0):
        self.feed = feed
        self.interval = interval
        self.clock = clock
        self.probe = probe
        self.service_every = service_every
        self._lock = threading.RLock()
        self._stages: dict[str, StageState] = {s.id: StageState() for s in STAGES}
        self._history = {s.id: History(BUCKET_SECONDS, HISTORY_SECONDS // BUCKET_SECONDS) for s in STAGES}
        self._events: deque[dict] = deque(maxlen=EVENTS_KEPT)
        self._services: list[dict] = []
        self._services_at = float("-inf")
        self._backfilled = False
        self._poll_error: str | None = None
        self._failures = 0
        self._generated_at = clock()
        self._subs: set[Subscription] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- polling ----
    def step(self, now: float | None = None) -> None:
        now = self.clock() if now is None else now
        if not self._backfilled:
            self._backfill(now)
        try:
            result = self.feed.poll(now)
        except Exception as e:  # never let one bad poll kill the stream
            logger.warning("feed poll failed: %s", e, exc_info=logger.isEnabledFor(logging.DEBUG))
            with self._lock:
                self._poll_error = f"{type(e).__name__}: {e}"
                self._failures += 1
                self._generated_at = now
            self._refresh_services(now)
            self._publish(now, [])
            return
        with self._lock:
            self._poll_error = None
            self._failures = 0
            for stage_id, state in result.stages.items():
                if stage_id in self._stages:
                    self._stages[stage_id] = state
                    self._history[stage_id].add(now, state.count)
            self._events.extend(result.events)
            self._generated_at = now
        self._refresh_services(now)
        self._publish(now, result.events)

    def _backfill(self, now: float) -> None:
        self._backfilled = True
        try:
            points = self.feed.backfill(now, HISTORY_SECONDS, BUCKET_SECONDS)
        except Exception as e:
            logger.warning("feed backfill failed: %s", e)
            return
        with self._lock:
            for stage_id, series in (points or {}).items():
                if stage_id in self._history:
                    self._history[stage_id].seed(series)

    def _refresh_services(self, now: float) -> None:
        if now - self._services_at < self.service_every:
            return
        rows = _rows_or_unknown("feed", "Data feed", now, lambda: self.feed.services(now))
        if self.probe is not None:
            rows += _rows_or_unknown("probe", "Service checks", now, self.probe)
        with self._lock:
            self._services = rows
            self._services_at = now

    # ---- views ----
    def _stage_dicts(self, now: float) -> list[dict]:
        out = []
        for stage in STAGES:
            st = self._stages[stage.id]
            rate = self._history[stage.id].rate_per_min(now)
            out.append({
                "id": stage.id, "title": stage.title, "table": stage.table,
                "status": st.status, "error": st.error, "count": st.count,
                "rate_per_min": None if rate is None else round(rate, 2),
                "last_write": st.last_write, "freshness": freshness(st.last_write, now),
                "version": st.version, "errors": st.errors,
            })
        return out

    def snapshot(self) -> dict:
        with self._lock:
            now = self._generated_at
            recent = list(self._events)[-RECENT_IN_SNAPSHOT:]
            recent.reverse()
            return {
                "mode": getattr(self.feed, "mode", "live"),
                "generated_at": now,
                "interval": self.interval,
                "poll_error": self._poll_error,
                "stages": self._stage_dicts(now),
                "edges": [{"from": a, "to": b} for a, b in EDGES],
                "services": list(self._services),
                "series": {sid: h.series() for sid, h in self._history.items()},
                "recent": recent,
            }

    def _tick(self, now: float, events: list[dict]) -> dict:
        with self._lock:
            points = {}
            for sid, h in self._history.items():
                series = h.series()
                if series:
                    points[sid] = series[-1]
            return {"generated_at": now, "poll_error": self._poll_error,
                    "stages": self._stage_dicts(now), "services": list(self._services),
                    "series_point": points, "events": list(events)}

    # ---- subscribers ----
    def subscribe(self) -> Subscription:
        sub = Subscription()
        sub.offer("snapshot", self.snapshot(), self.snapshot)
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subs.discard(sub)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)

    def _publish(self, now: float, events: list[dict]) -> None:
        with self._lock:
            subs = list(self._subs)
        if not subs:
            return
        tick = self._tick(now, events)
        for sub in subs:
            sub.offer("tick", tick, self.snapshot)

    # ---- background loop ----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ccfeed-hub", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:  # pragma: no cover - step already contains feed errors
                logger.exception("hub step crashed")
            wait = self.interval * (2 ** min(self._failures, 5)) if self._failures else self.interval
            self._stop.wait(min(wait, MAX_BACKOFF))


def _rows_or_unknown(row_id: str, label: str, now: float, fn) -> list[dict]:
    try:
        return list(fn() or [])
    except Exception as e:
        return [{"id": row_id, "label": label, "status": "unknown",
                 "detail": f"{type(e).__name__}: {e}", "checked_at": now, "data": {}}]
