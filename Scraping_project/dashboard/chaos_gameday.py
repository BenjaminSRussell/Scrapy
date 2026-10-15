"""Staging-only chaos game-day stubs (#967).

Control Center buttons call this module. A stub records that an operator asked
for a fault injection. It does not connect to Redis or Kafka and it does not
stop a process. Production stays off even if the arming flag is set.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping

ACTIONS = ("kill-redis", "pause-kafka")
POSTMORTEM = "chaos-gameday-postmortem.txt"
_TRUE = frozenset({"1", "true", "yes", "on"})
_PRODUCTION = frozenset({"production", "prod"})
_JOURNAL_MAX = 50

_lock = threading.Lock()
_journal: list[dict[str, str]] = []


def _text(env: Mapping[str, str], key: str) -> str:
    return str(env.get(key) or "").strip().lower()


def deploy_env(env: Mapping[str, str]) -> str:
    """Effective environment. ``production`` / ``prod`` always win.

    ``ENV`` is preferred over ``APP_ENV`` (same order as the destructive-op
    guard). ``CC_ENV`` is the dashboard label, but it cannot override a
    production pipeline environment.
    """
    pipeline = _text(env, "ENV") or _text(env, "APP_ENV")
    if pipeline in _PRODUCTION:
        return pipeline
    cc = _text(env, "CC_ENV")
    if cc in _PRODUCTION:
        return cc
    return cc or pipeline or "unset"


def armed(env: Mapping[str, str]) -> bool:
    return _text(env, "CC_CHAOS_GAMEDAY") in _TRUE


def enabled(env: Mapping[str, str]) -> bool:
    return deploy_env(env) == "staging" and armed(env)


def status(env: Mapping[str, str]) -> dict:
    on = enabled(env)
    return {
        "ok": True,
        "enabled": on,
        "env": deploy_env(env),
        "staging_only": True,
        "stub": True,
        "effect": "none",
        "actions": list(ACTIONS) if on else [],
        "postmortem": POSTMORTEM,
    }


def reset_journal() -> None:
    with _lock:
        _journal.clear()


def journal() -> list[dict[str, str]]:
    with _lock:
        return list(_journal)


def inject(env: Mapping[str, str], action: str) -> tuple[int, dict]:
    """Accept a known stub only when the game day is armed in staging."""
    refused = {
        "ok": False,
        "stub": True,
        "effect": "none",
        "postmortem": POSTMORTEM,
    }
    if not enabled(env):
        refused["error"] = "chaos game-day is off unless the environment is staging and CC_CHAOS_GAMEDAY is on"
        return 403, refused
    if action not in ACTIONS:
        refused["error"] = "unknown action"
        return 400, refused
    entry = {"action": action, "env": "staging", "stub": "true", "effect": "none"}
    with _lock:
        _journal.append(entry)
        if len(_journal) > _JOURNAL_MAX:
            del _journal[:-_JOURNAL_MAX]
    return 200, {
        "ok": True,
        "accepted": action,
        "stub": True,
        "effect": "none",
        "recorded": True,
        "postmortem": POSTMORTEM,
    }
