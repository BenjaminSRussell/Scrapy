"""One safety policy for destructive lake/queue operations (#522, #573, #576).

Every CLI path that wipes the Delta lake or drains Redis queues goes through
``authorize``:

1. **Dry-run by default.** Without ``--confirm`` nothing is touched. The plan
   (tables with row/byte estimates, or queues with sizes) is printed and the
   caller exits non-zero, so a forgotten flag is a visible no-op.
2. **Dual confirmation.** ``--confirm`` plus a typed confirmation (``yes``).
   For automation, ``--yes`` replaces the prompt only when ``ALLOW_LAKE_RESET=1``
   is also set in the environment.
3. **Production break-glass.** When ``ENV`` (or ``APP_ENV``) is ``production``
   or ``prod``, the operation is refused unless ``--i-know-what-im-doing`` *and*
   ``ALLOW_LAKE_RESET=1`` are given, and the operator types ``production``
   (no ``--yes`` shortcut in production).
4. **Audit trail.** Every decision (dry_run / refused / authorized / completed /
   failed) is appended as one JSON line to ``DESTRUCTIVE_AUDIT_LOG``
   (default ``data/logs/destructive_ops.jsonl``) with actor, host, time and
   targets, and is logged at WARNING.

Library calls (e.g. the Helm preStop hook's ``LakeDrainer().drain_transient_queues()``)
are not affected; the guard lives at the CLI boundary.
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import shutil
import socket
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOW_ENV = "ALLOW_LAKE_RESET"
AUDIT_ENV = "DESTRUCTIVE_AUDIT_LOG"
DEFAULT_AUDIT_LOG = PROJECT_ROOT / "data" / "logs" / "destructive_ops.jsonl"
PRODUCTION_ENVS = frozenset({"production", "prod"})
BREAK_GLASS_FLAG = "--i-know-what-im-doing"

# Exit codes shared by the scripts.
EXIT_DRY_RUN = 2  # nothing done because --confirm was missing
EXIT_REFUSED = 3  # refused by policy (production without break-glass, failed confirmation)


@dataclass
class Decision:
    proceed: bool
    outcome: str  # dry_run | refused | authorized
    reason: str
    targets: list[dict[str, Any]] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        if self.proceed:
            return 0
        return EXIT_DRY_RUN if self.outcome == "dry_run" else EXIT_REFUSED


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def current_env(env: Mapping[str, str] | None = None) -> str:
    e = _env(env)
    return (e.get("ENV") or e.get("APP_ENV") or "development").strip().lower()


def is_production(env: Mapping[str, str] | None = None) -> bool:
    return current_env(env) in PRODUCTION_ENVS


def actor(env: Mapping[str, str] | None = None) -> str:
    e = _env(env)
    for key in ("SUDO_USER", "USER", "LOGNAME", "USERNAME"):
        if e.get(key):
            return str(e[key])
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover - no passwd entry in some containers
        return "unknown"


def audit_path(env: Mapping[str, str] | None = None) -> Path:
    custom = _env(env).get(AUDIT_ENV)
    return Path(custom) if custom else DEFAULT_AUDIT_LOG


def audit(action: str, outcome: str, *, targets: list[dict[str, Any]] | None = None,
          env: Mapping[str, str] | None = None, **extra: Any) -> dict[str, Any]:
    """Append one audit record. Never raises: a failed write is logged instead."""
    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "action": action,
        "outcome": outcome,
        "actor": actor(env),
        "host": socket.gethostname(),
        "env": current_env(env),
        "argv": list(sys.argv),
        "targets": targets or [],
    }
    record.update(extra)
    logger.warning("destructive-op audit: %s %s by %s on %s (%d targets)",
                   action, outcome, record["actor"], record["env"], len(record["targets"]))
    path = audit_path(env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        logger.error("could not write destructive-op audit log %s: %s", path, exc)
    return record


def lake_targets(base: Path) -> list[dict[str, Any]]:
    """Tables under ``base`` with row/byte estimates from the Delta log (no data scan)."""
    base = Path(base)
    if not base.exists():
        return []
    out: list[dict[str, Any]] = []
    for log_dir in sorted(base.rglob("_delta_log")):
        table = log_dir.parent
        entry: dict[str, Any] = {"table": str(table.relative_to(base)), "rows": None, "bytes": None}
        try:
            from deltalake import DeltaTable

            batch = DeltaTable(str(table)).get_add_actions(flatten=True)
            if not hasattr(batch, "to_pydict"):  # deltalake>=1.0 returns an arro3 batch
                import pyarrow as pa

                batch = pa.record_batch(batch)
            actions = batch.to_pydict()
            rows = actions.get("num_records") or []
            entry["rows"] = int(sum(r for r in rows if r is not None))
            entry["bytes"] = int(sum(actions.get("size_bytes") or []))
        except Exception as exc:  # unreadable table still gets listed
            entry["error"] = str(exc)[:200]
        out.append(entry)
    return out


def format_targets(targets: list[dict[str, Any]]) -> str:
    if not targets:
        return "  (nothing to remove)"
    lines = []
    for t in targets:
        name = t.get("table") or t.get("queue") or t.get("path") or "?"
        rows = t.get("rows")
        size = t.get("bytes")
        rows_s = "?" if rows is None else f"{rows:,}"
        size_s = "" if size is None else f", {size:,} bytes"
        lines.append(f"  - {name}: {rows_s} rows{size_s}")
    return "\n".join(lines)


def add_guard_arguments(parser: Any, *, include_yes: bool = True) -> None:
    """Add --confirm / --yes / --i-know-what-im-doing to an argparse parser."""
    parser.add_argument("--confirm", action="store_true",
                        help="Actually perform the destructive operation (default is a dry-run plan)")
    if include_yes:
        parser.add_argument("--yes", "-y", action="store_true",
                            help=f"Skip the typed confirmation (only honoured with {ALLOW_ENV}=1, never in production)")
    parser.add_argument(BREAK_GLASS_FLAG, dest="break_glass", action="store_true",
                        help=f"Break-glass for ENV=production (also needs {ALLOW_ENV}=1 and typing 'production')")


def authorize(action: str, *, targets: list[dict[str, Any]], confirm: bool,
              assume_yes: bool = False, break_glass: bool = False,
              env: Mapping[str, str] | None = None,
              input_fn: Callable[[str], str] = input,
              interactive: bool | None = None,
              out: Callable[[str], None] = print) -> Decision:
    """Apply the policy in the module docstring and record the decision."""
    e = _env(env)
    prod = is_production(e)
    allowed_by_env = e.get(ALLOW_ENV, "") == "1"
    out(f"Plan for {action} (ENV={current_env(e)}):\n{format_targets(targets)}")

    def done(proceed: bool, outcome: str, reason: str) -> Decision:
        audit(action, outcome, targets=targets, env=e, reason=reason)
        if not proceed:
            out(("DRY RUN: " if outcome == "dry_run" else "REFUSED: ") + reason)
        return Decision(proceed, outcome, reason, targets)

    if not confirm:
        return done(False, "dry_run", "nothing was changed; re-run with --confirm to execute")
    if prod and not (break_glass and allowed_by_env):
        return done(False, "refused",
                    f"ENV={current_env(e)} is production: needs {BREAK_GLASS_FLAG} and {ALLOW_ENV}=1")
    if assume_yes and not prod:
        if allowed_by_env:
            return done(True, "authorized", f"--confirm --yes with {ALLOW_ENV}=1")
        return done(False, "refused", f"--yes needs {ALLOW_ENV}=1 in the environment (or drop --yes and type the confirmation)")

    phrase = "production" if prod else "yes"
    if interactive is None:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    if not interactive:
        return done(False, "refused", "typed confirmation required but stdin is not a terminal"
                    + ("" if prod else f" (use --yes with {ALLOW_ENV}=1 for automation)"))
    try:
        answer = input_fn(f"Type '{phrase}' to {action}: ")
    except EOFError:
        answer = ""
    if answer.strip() != phrase:
        return done(False, "refused", "typed confirmation did not match")
    return done(True, "authorized", f"--confirm + typed '{phrase}'" + (" (break-glass)" if prod else ""))


def backup_tree(src: Path, backup_dir: Path | None) -> Path | None:
    """Copy ``src`` to ``backup_dir/<name>-<UTC timestamp>`` before a wipe. Returns the copy."""
    if backup_dir is None or not Path(src).exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = Path(backup_dir) / f"{Path(src).name}-{stamp}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest)
    logger.warning("backed up %s to %s (restore: stop workers, move it back to %s)", src, dest, src)
    return dest


def guard_flags(args: Any) -> dict[str, bool]:
    """Read guard flags from argparse args; the legacy ``--force`` means ``--confirm --yes``."""
    force = bool(getattr(args, "force", False))
    if force:
        logger.warning("--force is deprecated: it now means --confirm --yes (still needs %s=1)", ALLOW_ENV)
    return {
        "confirm": bool(getattr(args, "confirm", False)) or force,
        "assume_yes": bool(getattr(args, "yes", False)) or force,
        "break_glass": bool(getattr(args, "break_glass", False)),
    }


def guarded_lake_wipe(base: Path, args: Any, *, action: str = "wipe the Delta lake",
                      env: Mapping[str, str] | None = None,
                      input_fn: Callable[[str], str] = input,
                      interactive: bool | None = None) -> Decision:
    """Authorize, optionally back up, then delete ``base``. Callers exit with ``decision.exit_code``."""
    base = Path(base)
    targets = lake_targets(base)
    flags = guard_flags(args)
    decision = authorize(action, targets=targets, env=env, input_fn=input_fn, interactive=interactive,
                         confirm=flags["confirm"], assume_yes=flags["assume_yes"],
                         break_glass=flags["break_glass"])
    if not decision.proceed:
        return decision
    backup = None
    try:
        backup = backup_tree(base, getattr(args, "backup_dir", None))
        if base.exists():
            shutil.rmtree(base)
    except Exception as exc:
        audit(action, "failed", targets=targets, env=env, error=str(exc)[:500])
        raise
    audit(action, "completed", targets=targets, env=env, backup=str(backup) if backup else None)
    return decision


def add_backup_argument(parser: Any) -> None:
    parser.add_argument("--backup-dir", type=Path, default=None,
                        help="Copy the lake to BACKUP_DIR/<name>-<timestamp> before wiping (restore = move it back)")
