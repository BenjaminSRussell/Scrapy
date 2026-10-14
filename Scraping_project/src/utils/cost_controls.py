"""Daily Stage 3/4 budget caps (#528).

A missing ``cost_controls`` block, a null block, or a null cap is uncapped:
workers do not stop. A number is a per-UTC-day ceiling. When nothing remains
(or the next document's known OCR page count does not fit), the worker stops
that batch with status ``budget_exhausted`` and does not call a GPU, OCR,
network, or paid API.

GPU minutes are the clock delta around the stage's expensive call, so tests
can inject a fake clock. OCR pages come from the document (``ocr_pages``,
``page_count``, ``pages``, otherwise one page for a PDF and zero otherwise).

Spend lives in a process-wide ledger. ``run_stage3_worker`` / ``run_stage4_worker``
build a new worker each cycle; the day's spend has to survive that. A new UTC
day (the injected clock's date) starts again at the full cap. The gauge is
``budget_remaining{stage,resource}`` and is set only for numeric caps.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

GPU_MINUTES = "gpu_minutes"
OCR_PAGES = "ocr_pages"
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"

STAGE3_GPU_MINUTES_KEY = "cost_controls.stage3_max_gpu_minutes_per_day"
STAGE4_GPU_MINUTES_KEY = "cost_controls.stage4_max_gpu_minutes_per_day"
STAGE4_OCR_PAGES_KEY = "cost_controls.stage4_max_ocr_pages_per_day"

_CAP_KEYS = {
    ("stage3", GPU_MINUTES): STAGE3_GPU_MINUTES_KEY,
    ("stage4", GPU_MINUTES): STAGE4_GPU_MINUTES_KEY,
    ("stage4", OCR_PAGES): STAGE4_OCR_PAGES_KEY,
}

# (stage, resource, UTC date) -> amount spent. Shared by every budget that
# does not pass its own ledger (tests do, so cases stay isolated).
_LEDGER: dict[tuple[str, str, str], float] = {}

try:
    from prometheus_client import Gauge

    BUDGET_REMAINING: Any = Gauge(
        "budget_remaining",
        "Remaining daily Stage 3/4 budget. gpu_minutes are minutes; ocr_pages are pages (#528).",
        ["stage", "resource"],
    )
except Exception:  # prometheus_client missing or metric already registered
    BUDGET_REMAINING = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def reset_ledger_for_tests() -> None:
    """Drop process-wide spend. Tests that use the default ledger call this."""
    _LEDGER.clear()


def resources_for(stage: str) -> tuple[str, ...]:
    if stage == "stage3":
        return (GPU_MINUTES,)
    if stage == "stage4":
        return (GPU_MINUTES, OCR_PAGES)
    return ()


def parse_cap(value: Any) -> float | None:
    """A finite cap, or None when the setting is missing, null, or not a number.

    Booleans are not numbers here (``True`` must not become ``1``). A negative
    number is ``0`` so a bad cap cannot keep spending.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number == float("inf") or number == float("-inf"):
        return None
    if number < 0:
        return 0.0
    return number


def configured_cap(dotted_key: str) -> Any:
    """Raw config value. Unreadable config is uncapped (does not stop the worker)."""
    try:
        from src.core.config import get_config

        return get_config().get(dotted_key, None)
    except Exception:
        logger.debug("cost cap %s unreadable; treating as uncapped", dotted_key, exc_info=True)
        return None


def ocr_pages_for(doc: Mapping[str, Any]) -> float:
    """Pages this document would OCR. Non-PDFs without an explicit count are 0."""
    for key in ("ocr_pages", "page_count", "pages"):
        number = parse_cap(doc.get(key))
        if number is not None and number > 0:
            return number
    url = str(doc.get("url") or "")
    is_pdf = (
        bool(doc.get("is_pdf"))
        or doc.get("content_hint") == "pdf"
        or url.lower().split("?", 1)[0].endswith(".pdf")
    )
    return 1.0 if is_pdf else 0.0


class CostBudget:
    """Remaining daily budget for one process.

    ``caps`` overrides config when it is not None (missing keys are uncapped).
    ``ledger`` overrides the process-wide spend map when it is not None.
    """

    def __init__(
        self,
        clock: Any = None,
        caps: Mapping[str, Any] | None = None,
        ledger: dict[tuple[str, str, str], float] | None = None,
    ) -> None:
        self._clock = _utc_now if clock is None else clock
        self._caps = None if caps is None else dict(caps)
        self._spent = _LEDGER if ledger is None else ledger

    def now(self) -> datetime:
        current = self._clock()
        if not isinstance(current, datetime):
            current = _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        return current.astimezone(UTC)

    def day_key(self) -> str:
        return self.now().date().isoformat()

    def cap(self, stage: str, resource: str) -> float | None:
        key = _CAP_KEYS.get((stage, resource))
        if key is None:
            return None
        if self._caps is not None:
            raw = self._caps.get(key, None)
        else:
            raw = configured_cap(key)
        return parse_cap(raw)

    def spent(self, stage: str, resource: str) -> float:
        return float(self._spent.get((stage, resource, self.day_key()), 0.0))

    def remaining(self, stage: str, resource: str) -> float | None:
        limit = self.cap(stage, resource)
        if limit is None:
            return None
        return max(0.0, limit - self.spent(stage, resource))

    def charge(self, stage: str, resource: str, amount: float) -> float | None:
        """Add today's usage. ``None`` when that resource is uncapped. Never below zero."""
        limit = self.cap(stage, resource)
        if limit is None:
            return None
        if amount > 0:
            key = (stage, resource, self.day_key())
            self._spent[key] = self.spent(stage, resource) + float(amount)
        left = self.remaining(stage, resource)
        if left is not None:
            self._publish(stage, resource, left)
        return left

    def block_reason(self, stage: str, upcoming: Mapping[str, float] | None = None) -> str | None:
        """``budget_exhausted`` when a numeric cap cannot cover the next call, else None.

        Uncapped resources are ignored. A known OCR demand larger than the
        remaining pages stops before that call. GPU minutes stop once nothing
        remains; the call's duration is only known afterwards.
        """
        upcoming = upcoming or {}
        tripped: tuple[str, float, float] | None = None
        for resource in resources_for(stage):
            limit = self.cap(stage, resource)
            if limit is None:
                continue
            left = self.remaining(stage, resource)
            if left is None:
                continue
            self._publish(stage, resource, left)
            demand = upcoming.get(resource, 0.0)
            try:
                demand_f = float(demand)
            except (TypeError, ValueError):
                demand_f = 0.0
            if isinstance(demand, bool):
                demand_f = 0.0
            if tripped is None and (left <= 0 or (demand_f > 0 and demand_f > left)):
                tripped = (resource, left, limit)
        if tripped is None:
            return None
        resource, left, limit = tripped
        logger.warning(
            "[%s] status=%s stopping batch; %s remaining=%.6g cap=%.6g; "
            "not calling GPU, OCR, network, or paid API",
            stage.upper(),
            STATUS_BUDGET_EXHAUSTED,
            resource,
            left,
            limit,
        )
        return STATUS_BUDGET_EXHAUSTED

    def _publish(self, stage: str, resource: str, remaining: float) -> None:
        if BUDGET_REMAINING is None:
            return
        try:
            BUDGET_REMAINING.labels(stage=stage, resource=resource).set(remaining)
        except Exception:
            logger.debug("budget_remaining update failed", exc_info=True)


def has_numeric_cap(budget: CostBudget, stage: str) -> bool:
    return any(budget.cap(stage, resource) is not None for resource in resources_for(stage))


def ensure_budget(worker: Any) -> CostBudget:
    budget = getattr(worker, "_cost_budget_obj", None)
    if isinstance(budget, CostBudget):
        return budget
    budget = CostBudget()
    worker._cost_budget_obj = budget
    return budget


def stop_for_budget(worker: Any, stage: str, upcoming: Mapping[str, float] | None = None) -> bool:
    """True when the batch must stop. Sets ``worker.budget_status`` to ``budget_exhausted``."""
    reason = ensure_budget(worker).block_reason(stage, upcoming)
    if reason is None:
        return False
    worker.budget_status = reason
    return True
