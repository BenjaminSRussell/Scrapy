"""Rough kWh estimate from job runtime (#949).

kWh is stated watts times hours. The default is one worker at 100 W
(``JOB_ENERGY_WATTS``). This is not a watt-meter reading, not a carbon
inventory, and not a cloud bill. Nothing here contacts a network or delays a job.
"""

from __future__ import annotations

import math
import os
from typing import Any

DEFAULT_JOB_WATTS = 100.0
JOB_ENERGY_WATTS_ENV = "JOB_ENERGY_WATTS"
_JOULES_PER_KWH = 3_600_000.0

ENERGY_DISCLAIMER = (
    "Rough estimate only: runtime hours times a stated wattage (default 100 W, "
    "JOB_ENERGY_WATTS). Not a measured kWh reading, not a carbon inventory, and not a cloud bill."
)


def stated_watts(raw: str | None = None) -> float:
    """Non-negative stated watts. Blank, non-numeric, or negative uses the default."""
    if raw is None:
        raw = os.environ.get(JOB_ENERGY_WATTS_ENV, "")
    text = str(raw).strip()
    if not text:
        return DEFAULT_JOB_WATTS
    try:
        value = float(text)
    except (TypeError, ValueError):
        return DEFAULT_JOB_WATTS
    if isinstance(raw, bool) or not math.isfinite(value) or value < 0:
        return DEFAULT_JOB_WATTS
    return value


def _runtime_seconds(runtime_seconds: Any) -> float | None:
    if isinstance(runtime_seconds, bool) or not isinstance(runtime_seconds, (int, float)):
        return None
    value = float(runtime_seconds)
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _watts(watts: float | None) -> float:
    if watts is None:
        return stated_watts()
    if isinstance(watts, bool) or not isinstance(watts, (int, float)):
        return stated_watts()
    value = float(watts)
    if not math.isfinite(value) or value < 0:
        return stated_watts()
    return value


def estimate_kwh(runtime_seconds: float, watts: float | None = None) -> float | None:
    """kWh from seconds. ``None`` when runtime is missing, negative, or not a number.

    A bool is not a duration. Zero seconds is zero kWh. Invalid watts fall
    back to :func:`stated_watts` instead of inventing a draw.
    """
    seconds = _runtime_seconds(runtime_seconds)
    if seconds is None:
        return None
    return _watts(watts) * seconds / _JOULES_PER_KWH


def format_energy_badge(kwh: float | None) -> str:
    """Short badge text. ``n/a`` when there is no estimate."""
    if isinstance(kwh, bool) or not isinstance(kwh, (int, float)):
        return "n/a"
    value = float(kwh)
    if not math.isfinite(value) or value < 0:
        return "n/a"
    if value == 0:
        return "≈ 0 kWh"
    if value < 0.001:
        return "≈ <0.001 kWh"
    if value < 1:
        return f"≈ {value:.3f} kWh"
    if value < 100:
        return f"≈ {value:.2f} kWh"
    return f"≈ {value:.0f} kWh"


def job_energy_scorecard(
    crawl_job_id: str | None,
    runtime_seconds: float,
    watts: float | None = None,
) -> dict[str, Any]:
    """One job's scorecard: kWh badge plus the disclaimer.

    An unusable runtime still returns the disclaimer and a ``n/a`` badge.
    It does not invent a kWh figure.
    """
    seconds = _runtime_seconds(runtime_seconds)
    used = _watts(watts)
    kwh = None if seconds is None else used * seconds / _JOULES_PER_KWH
    job = str(crawl_job_id or "").strip() or "unknown"
    return {
        "scorecard": "job_energy",
        "crawl_job_id": job,
        "runtime_seconds": seconds,
        "watts": used,
        "kwh": kwh,
        "badge": format_energy_badge(kwh),
        "disclaimer": ENERGY_DISCLAIMER,
    }
