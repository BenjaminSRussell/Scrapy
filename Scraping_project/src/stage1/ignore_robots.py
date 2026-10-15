"""Stage 1 dual confirmation for rustmapper ``ignore_robots`` (#938).

rustmapper and Stage 1 both default to obeying robots.txt. A rustmapper
``ignore_robots`` true must not turn Stage 1 obedience off unless the operator
also confirms on the Stage 1 CLI or Control Center page. Authorized and
refused decisions are appended to an audit JSONL file. This module does not
fetch robots.txt and does not start a crawl.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.timeutil import utc_now_iso

logger = logging.getLogger(__name__)

CONFIRM_PHRASE = "IGNORE ROBOTS"
ALLOW_ENV = "ALLOW_IGNORE_ROBOTS"
AUDIT_ENV = "IGNORE_ROBOTS_AUDIT_LOG"
DECISION_ENV = "IGNORE_ROBOTS_DECISION_FILE"
RUSTMAPPER_ENV = "RUSTMAPPER_IGNORE_ROBOTS"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off", ""})

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AUDIT_LOG = _PROJECT_ROOT / "data" / "logs" / "ignore_robots.jsonl"


def as_bool(value: Any, default: bool = False) -> bool:
    """Parse a flag. Unknown values stay at ``default`` (fail closed for ignore)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
        return default
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE:
            return True
        if token in _FALSE:
            return False
        return default
    return default


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def audit_path(env: Mapping[str, str] | None = None, override: Path | None = None) -> Path:
    if override is not None:
        return Path(override)
    custom = _env(env).get(AUDIT_ENV)
    return Path(custom) if custom else DEFAULT_AUDIT_LOG


def actor_name(env: Mapping[str, str] | None = None, explicit: str | None = None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()[:120]
    e = _env(env)
    for key in ("SUDO_USER", "USER", "LOGNAME", "USERNAME"):
        if e.get(key):
            return str(e[key])[:120]
    return "operator"


@dataclass(frozen=True)
class RobotsDecision:
    """Whether Stage 1 may ignore robots.txt for a rustmapper handoff."""

    robotstxt_obey: bool
    ignore_robots: bool
    outcome: str  # obey | refused | authorized
    detail: str
    rustmapper_ignore_robots: bool
    stage1_confirm: bool
    reason: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "robotstxt_obey": self.robotstxt_obey,
            "ignore_robots": self.ignore_robots,
            "outcome": self.outcome,
            "detail": self.detail,
            "rustmapper_ignore_robots": self.rustmapper_ignore_robots,
            "stage1_confirm": self.stage1_confirm,
            "reason": self.reason,
            "source": self.source,
        }


def _append_audit(record: dict[str, Any], path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        logger.error("could not write ignore-robots audit log %s: %s", path, exc)


def decide(
    *,
    rustmapper_ignore_robots: Any = False,
    stage1_confirm: Any = False,
    typed: str | None = None,
    reason: str = "",
    assume_yes: bool = False,
    source: str = "cli",
    actor: str | None = None,
    env: Mapping[str, str] | None = None,
    audit_path_override: Path | None = None,
    record: bool = True,
) -> RobotsDecision:
    """Apply the dual-confirm policy and optionally append one audit line.

    ``ignore_robots`` is true only when rustmapper requested it, Stage 1
    confirmed, a non-empty reason is present, and the operator typed
    ``IGNORE ROBOTS`` (or ``--yes`` with ``ALLOW_IGNORE_ROBOTS=1``).
    """
    requested = as_bool(rustmapper_ignore_robots, default=False)
    confirmed = as_bool(stage1_confirm, default=False)
    why = (reason or "").strip()[:500]
    phrase = (typed or "").strip()
    e = _env(env)
    allowed_by_env = e.get(ALLOW_ENV, "") == "1"
    phrase_ok = phrase == CONFIRM_PHRASE or (assume_yes and allowed_by_env and not phrase)

    if not requested and not confirmed:
        outcome, detail, ignore = "obey", "default: both sides obey robots.txt", False
    elif requested and not confirmed:
        outcome, detail, ignore = (
            "refused",
            "rustmapper ignore_robots is not inherited; Stage1 confirm is required",
            False,
        )
    elif confirmed and not requested:
        outcome, detail, ignore = (
            "refused",
            "Stage1 confirm has no effect unless rustmapper ignore_robots is also set",
            False,
        )
    elif not why:
        outcome, detail, ignore = "refused", "a reason is required to ignore robots.txt", False
    elif assume_yes and not allowed_by_env and phrase != CONFIRM_PHRASE:
        outcome, detail, ignore = (
            "refused",
            f"--yes needs {ALLOW_ENV}=1 (or type {CONFIRM_PHRASE!r})",
            False,
        )
    elif not phrase_ok:
        outcome, detail, ignore = (
            "refused",
            f"typed confirmation must be {CONFIRM_PHRASE!r}",
            False,
        )
    else:
        outcome, detail, ignore = "authorized", "rustmapper ignore_robots and Stage1 confirm", True

    decision = RobotsDecision(
        robotstxt_obey=not ignore,
        ignore_robots=ignore,
        outcome=outcome,
        detail=detail,
        rustmapper_ignore_robots=requested,
        stage1_confirm=confirmed,
        reason=why,
        source=source if source in {"cli", "ui"} else "cli",
    )
    if record:
        audit_record = {
            "ts": utc_now_iso(),
            "action": "ignore_robots",
            "outcome": decision.outcome,
            "actor": actor_name(e, actor),
            "source": decision.source,
            "rustmapper_ignore_robots": requested,
            "stage1_confirm": confirmed,
            "reason": why,
            "robotstxt_obey": decision.robotstxt_obey,
            "detail": detail,
        }
        _append_audit(audit_record, audit_path(e, audit_path_override))
        logger.warning(
            "ignore-robots audit: %s robotstxt_obey=%s source=%s",
            decision.outcome,
            decision.robotstxt_obey,
            decision.source,
        )
    return decision


def read_audit(path: Path | None = None, *, env: Mapping[str, str] | None = None, limit: int = 50) -> list[dict[str, Any]]:
    log = audit_path(env, path)
    if not log.exists():
        return []
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows[-limit:]


def write_decision_file(path: Path, decision: RobotsDecision) -> None:
    """Persist an authorized decision. Refusals do not write a file."""
    if not decision.ignore_robots or decision.outcome != "authorized":
        return
    payload = {
        "outcome": "authorized",
        "rustmapper_ignore_robots": True,
        "stage1_confirm": True,
        "reason": decision.reason,
        "phrase_ok": True,
        "robotstxt_obey": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def decision_file_authorizes(env: Mapping[str, str] | None = None, path: Path | None = None) -> bool:
    """True only for a complete authorized dual-confirm record. Missing file is false."""
    e = _env(env)
    raw = path if path is not None else e.get(DECISION_ENV) or ""
    if not raw:
        return False
    file = Path(str(raw))
    if not file.is_file():
        return False
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return (
        data.get("outcome") == "authorized"
        and as_bool(data.get("rustmapper_ignore_robots"), default=False)
        and as_bool(data.get("stage1_confirm"), default=False)
        and bool(str(data.get("reason") or "").strip())
        and as_bool(data.get("phrase_ok"), default=False)
        and data.get("robotstxt_obey") is False
    )


def apply_project_robotstxt_obey(
    explicit_obey: bool,
    *,
    rustmapper_ignore_robots: Any = None,
    config_ignore_robots: Any = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Project ``ROBOTSTXT_OBEY`` after the rustmapper inheritance guard.

    When rustmapper did not request ignore, ``explicit_obey`` is unchanged
    (the existing Stage 1 opt-out). When it did, Stage 1 keeps obeying unless
    ``IGNORE_ROBOTS_DECISION_FILE`` is an authorized dual-confirm record.
    """
    requested = as_bool(rustmapper_ignore_robots, default=False) or as_bool(config_ignore_robots, default=False)
    if not requested:
        return bool(explicit_obey)
    if decision_file_authorizes(env):
        return False
    return True


def ignore_robots_requested_in_jsonl(text: str) -> bool:
    """True when any JSONL object sets ``ignore_robots``. Absent means false."""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and as_bool(obj.get("ignore_robots"), default=False):
            return True
    return False
