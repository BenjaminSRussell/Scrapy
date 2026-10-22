"""Cluster-wide crawl kill switch and request/byte budgets (#456).

Stage 1 (``CrawlGuardMiddleware``) and Stage 2 (``Stage2Worker._analyze_url``)
consult one ``CrawlGuard`` before every download:

* **Kill switch.** A Redis hash ``crawl:kill_switch``, shared by every pod.
  Engaging it stops new downloads within ``kill_switch_check_secs`` (default
  5 s, the documented SLA). Stage 1 closes its spiders with reason
  ``kill_switch``; Stage 2 leaves URLs pending instead of failing them.
  ``CRAWL_KILL_SWITCH=1`` in the environment engages it without Redis, as a
  break-glass fallback.
* **Budgets.** Global per-day and per-crawl (``CRAWL_JOB_ID``) caps on
  requests and response bytes, counted in Redis with ``INCRBY`` so every
  worker shares one tally. 0 means unlimited. A spent budget acts like the
  kill switch for that scope.
* **Audit.** Every engage/release is appended to ``crawl:kill_switch:audit``
  (newest first, capped) with actor, reason, and time.

Config (``config.yml`` ``crawl_safety.*``; all optional): ``kill_switch_check_secs``,
``max_requests_per_day``, ``max_bytes_per_day``, ``per_crawl_max_requests``,
``per_crawl_max_bytes``, ``on_error`` (``closed`` by default, or ``open``), and
``max_stale_secs``.

Redis failures (#1530). With ``on_error=closed`` a worker that cannot read the
kill switch blocks once its last good read is older than ``max_stale_secs``, and
a failed budget read blocks. Charges that fail are buffered and reapplied on the
next refresh. A budget block is re-read every check interval and immediately when
the UTC day rolls over, so it clears without a restart.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

KILL_KEY = "crawl:kill_switch"
AUDIT_KEY = "crawl:kill_switch:audit"
AUDIT_MAX = 1000
BUDGET_PREFIX = "crawl:budget"
DAY_TTL_SECS = 2 * 86400
CRAWL_TTL_SECS = 14 * 86400
TRUTHY = ("1", "true", "yes", "on")
ON_ERROR_MODES = ("closed", "open")

try:
    from prometheus_client import Counter, Gauge

    KILL_SWITCH_ENGAGED: Any = Gauge("crawl_kill_switch_engaged", "1 while the global crawl kill switch is engaged")
    CRAWL_GUARD_BLOCKED: Any = Counter(
        "crawl_guard_blocked_total", "Downloads refused by the crawl guard", ["stage", "reason"]
    )
    CRAWL_GUARD_ERRORS: Any = Counter(
        "crawl_guard_redis_errors_total", "Crawl guard Redis reads/writes that failed (fail-closed or charge buffered)"
    )
except Exception:  # prometheus_client missing or already registered
    KILL_SWITCH_ENGAGED = CRAWL_GUARD_BLOCKED = CRAWL_GUARD_ERRORS = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _s(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def _default_client() -> Any:
    from src.utils.redis import get_redis

    helper = get_redis()
    return getattr(helper, "client", helper)


@dataclass
class SwitchState:
    engaged: bool = False
    reason: str = ""
    actor: str = ""
    at: str = ""
    source: str = "redis"


class KillSwitch:
    def __init__(
        self,
        client: Any = None,
        check_secs: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
        on_error: str = "closed",
        max_stale_secs: float = 30.0,
    ):
        if on_error not in ON_ERROR_MODES:
            raise ValueError(f"crawl_safety.on_error must be one of {ON_ERROR_MODES}, got {on_error!r}")
        self._client_obj = client
        self.check_secs = check_secs
        self.on_error = on_error
        self.max_stale_secs = max_stale_secs
        self._clock = clock
        self._state = SwitchState()
        self._checked_at: float | None = None
        self._read_at: float | None = None

    @property
    def client(self) -> Any:
        if self._client_obj is None:
            self._client_obj = _default_client()
        return self._client_obj

    def state(self, refresh: bool = False) -> SwitchState:
        if os.environ.get("CRAWL_KILL_SWITCH", "").strip().lower() in TRUTHY:
            return SwitchState(True, reason="CRAWL_KILL_SWITCH env", source="env")
        now = self._clock()
        if not refresh and self._checked_at is not None and now - self._checked_at < self.check_secs:
            return self._state
        try:
            raw = self.client.hgetall(KILL_KEY) or {}
            data = {_s(k): _s(v) for k, v in raw.items()}
            self._state = SwitchState(
                engaged=data.get("engaged", "") in TRUTHY,
                reason=data.get("reason", ""),
                actor=data.get("actor", ""),
                at=data.get("at", ""),
            )
            self._read_at = now
        except Exception as e:
            logger.warning(f"[CRAWL_GUARD] kill switch read failed (on_error={self.on_error}): {e}")
            if CRAWL_GUARD_ERRORS is not None:
                CRAWL_GUARD_ERRORS.inc()
            # Fail closed once the last good read is too old to trust. Until then keep it (no flapping on a blip).
            if self.on_error == "closed" and not self._read_recently(now):
                self._state = SwitchState(engaged=True, reason="kill switch unreadable; failing closed", source="error")
        self._checked_at = now
        if KILL_SWITCH_ENGAGED is not None:
            KILL_SWITCH_ENGAGED.set(1 if self._state.engaged else 0)
        return self._state

    def _read_recently(self, now: float) -> bool:
        return self._read_at is not None and now - self._read_at < self.max_stale_secs

    def engaged(self) -> bool:
        return self.state().engaged

    def _audit(self, action: str, actor: str, reason: str) -> None:
        entry = json.dumps({"action": action, "actor": actor, "reason": reason, "at": _utcnow()})
        pipe = self.client.pipeline()
        pipe.lpush(AUDIT_KEY, entry)
        pipe.ltrim(AUDIT_KEY, 0, AUDIT_MAX - 1)
        pipe.execute()

    def engage(self, reason: str, actor: str) -> SwitchState:
        if not reason.strip() or not actor.strip():
            raise ValueError("engaging the kill switch requires a reason and an actor")
        self.client.hset(KILL_KEY, mapping={"engaged": "1", "reason": reason, "actor": actor, "at": _utcnow()})
        self._audit("engage", actor, reason)
        logger.critical(f"[CRAWL_GUARD] KILL SWITCH ENGAGED by {actor}: {reason}")
        return self.state(refresh=True)

    def release(self, actor: str, reason: str = "") -> SwitchState:
        if not actor.strip():
            raise ValueError("releasing the kill switch requires an actor")
        self.client.delete(KILL_KEY)
        self._audit("release", actor, reason)
        logger.warning(f"[CRAWL_GUARD] kill switch released by {actor}: {reason}")
        return self.state(refresh=True)

    def audit(self, limit: int = 20) -> list[dict[str, Any]]:
        out = []
        for raw in self.client.lrange(AUDIT_KEY, 0, max(limit, 1) - 1) or []:
            try:
                out.append(json.loads(_s(raw)))
            except ValueError:
                out.append({"raw": _s(raw)})
        return out


class CrawlBudget:
    """Shared request/byte caps; 0 = unlimited."""

    def __init__(
        self,
        client: Any = None,
        max_requests_per_day: int = 0,
        max_bytes_per_day: int = 0,
        crawl_id: str | None = None,
        per_crawl_max_requests: int = 0,
        per_crawl_max_bytes: int = 0,
        day: Callable[[], str] = lambda: datetime.now(timezone.utc).strftime("%Y%m%d"),
    ):
        self._client_obj = client
        self.caps = {
            "daily_requests": max(int(max_requests_per_day or 0), 0),
            "daily_bytes": max(int(max_bytes_per_day or 0), 0),
            "crawl_requests": max(int(per_crawl_max_requests or 0), 0) if crawl_id else 0,
            "crawl_bytes": max(int(per_crawl_max_bytes or 0), 0) if crawl_id else 0,
        }
        self.crawl_id = crawl_id
        self._day = day

    @property
    def client(self) -> Any:
        if self._client_obj is None:
            self._client_obj = _default_client()
        return self._client_obj

    @property
    def enabled(self) -> bool:
        return any(self.caps.values())

    def _keys(self, day: str | None = None) -> dict[str, tuple[str, int]]:
        day = day or self._day()
        keys = {
            "daily_requests": (f"{BUDGET_PREFIX}:global:{day}:requests", DAY_TTL_SECS),
            "daily_bytes": (f"{BUDGET_PREFIX}:global:{day}:bytes", DAY_TTL_SECS),
        }
        if self.crawl_id:
            keys["crawl_requests"] = (f"{BUDGET_PREFIX}:crawl:{self.crawl_id}:requests", CRAWL_TTL_SECS)
            keys["crawl_bytes"] = (f"{BUDGET_PREFIX}:crawl:{self.crawl_id}:bytes", CRAWL_TTL_SECS)
        return keys

    def _over(self, usage: dict[str, int]) -> str | None:
        for name, cap in self.caps.items():
            if cap and usage.get(name, 0) >= cap:
                return name
        return None

    def usage(self) -> dict[str, int]:
        keys = self._keys()
        names = list(keys)
        values = self.client.mget([keys[n][0] for n in names])
        return {n: int(_s(v)) if v is not None else 0 for n, v in zip(names, values)}

    def exhausted(self) -> str | None:
        if not self.enabled:
            return None
        return self._over(self.usage())

    def charge(self, requests: int = 0, nbytes: int = 0, day: str | None = None) -> str | None:
        """Add usage; return the first exhausted cap name, if any. ``day`` charges an earlier day's counters."""
        if not self.enabled or (not requests and not nbytes):
            return None
        keys = self._keys(day)
        amounts = {
            "daily_requests": requests, "daily_bytes": nbytes,
            "crawl_requests": requests, "crawl_bytes": nbytes,
        }
        pipe = self.client.pipeline()
        names = [n for n in keys if amounts[n]]
        for n in names:
            key, ttl = keys[n]
            pipe.incrby(key, amounts[n])
            pipe.expire(key, ttl)
        results = pipe.execute()
        usage = {n: int(results[2 * i]) for i, n in enumerate(names)}
        return self._over(usage)


class CrawlGuard:
    """Kill switch + budget, with a cached budget check (same SLA as the switch)."""

    def __init__(
        self, switch: KillSwitch, budget: CrawlBudget | None = None, clock: Callable[[], float] = time.monotonic
    ):
        self.switch = switch
        self.budget = budget or CrawlBudget(client=switch._client_obj)
        self._clock = clock
        self._budget_reason: str | None = None
        self._budget_checked: float | None = None
        self._budget_day: str | None = None
        self._pending: dict[str, list[int]] = {}  # day -> [requests, nbytes] charges Redis has not accepted yet

    @classmethod
    def from_config(cls, config: Any = None, client: Any = None, crawl_id: str | None = None) -> "CrawlGuard":
        if config is None:
            try:
                from src.core.config import get_config

                config = get_config()
            except Exception:
                config = None

        def opt(key: str, default: Any) -> Any:
            try:
                return config.get(f"crawl_safety.{key}", default) if config is not None else default
            except Exception:
                return default

        crawl_id = crawl_id if crawl_id is not None else (os.environ.get("CRAWL_JOB_ID") or None)
        switch = KillSwitch(
            client=client,
            check_secs=float(opt("kill_switch_check_secs", 5.0)),
            on_error=str(opt("on_error", "closed")).strip().lower(),
            max_stale_secs=float(opt("max_stale_secs", 30.0)),
        )
        budget = CrawlBudget(
            client=client,
            max_requests_per_day=opt("max_requests_per_day", 0),
            max_bytes_per_day=opt("max_bytes_per_day", 0),
            crawl_id=crawl_id,
            per_crawl_max_requests=opt("per_crawl_max_requests", 0),
            per_crawl_max_bytes=opt("per_crawl_max_bytes", 0),
        )
        return cls(switch, budget)

    def block_reason(self, stage: str = "") -> str | None:
        """``"kill_switch"``, ``"budget:<cap>"`` or None (download allowed)."""
        reason: str | None = None
        if self.switch.engaged():
            reason = "kill_switch"
        elif self.budget.enabled:
            now = self._clock()
            day = self.budget._day()
            # Re-read on every check interval, and at once when the day rolls over, so a spent budget clears.
            if (
                day != self._budget_day
                or self._budget_checked is None
                or now - self._budget_checked >= self.switch.check_secs
            ):
                self._refresh_budget()
                self._budget_checked = now
                self._budget_day = day
            if self._budget_reason:
                reason = f"budget:{self._budget_reason}"
        if reason and CRAWL_GUARD_BLOCKED is not None:
            CRAWL_GUARD_BLOCKED.labels(stage=stage or "unknown", reason=reason.split(":")[0]).inc()
        return reason

    def _refresh_budget(self) -> None:
        self._flush_pending()
        try:
            self._budget_reason = self.budget.exhausted()
        except Exception as e:
            logger.warning(f"[CRAWL_GUARD] budget read failed (on_error={self.switch.on_error}): {e}")
            if CRAWL_GUARD_ERRORS is not None:
                CRAWL_GUARD_ERRORS.inc()
            if self.switch.on_error == "closed":
                self._budget_reason = "redis_unavailable"

    def charge(self, requests: int = 0, nbytes: int = 0) -> str | None:
        if not self.budget.enabled or (not requests and not nbytes):
            return None
        entry = self._pending.setdefault(self.budget._day(), [0, 0])
        entry[0] += requests
        entry[1] += nbytes
        over = self._flush_pending()
        if over:
            self._budget_reason = over
        return over

    def _flush_pending(self) -> str | None:
        """Write buffered charges, oldest day first. Stops at the first failure and keeps the rest for next time."""
        over = None
        for day in sorted(self._pending):
            requests, nbytes = self._pending[day]
            try:
                over = self.budget.charge(requests=requests, nbytes=nbytes, day=day) or over
            except Exception as e:
                logger.warning(f"[CRAWL_GUARD] budget charge failed, buffering {requests} requests / {nbytes} bytes: {e}")
                if CRAWL_GUARD_ERRORS is not None:
                    CRAWL_GUARD_ERRORS.inc()
                break
            del self._pending[day]
        return over

    def status(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kill_switch": asdict(self.switch.state(refresh=True))}
        if self.budget.enabled:
            out["budget"] = {"caps": self.budget.caps, "usage": self.budget.usage(), "crawl_id": self.budget.crawl_id}
        return out
