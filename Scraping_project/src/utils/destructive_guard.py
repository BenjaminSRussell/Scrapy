"""Guardrails for destructive lake operations (#522, #576).

Every tool that wipes, drains or overwrites lake data goes through this module:

* **Dry-run by default.** Nothing is changed unless the caller passes
  ``execute=True`` (``--execute`` on the CLIs); a dry run only reports what
  would be affected.
* **Dual confirmation.** Executing needs the ``--i-really-mean-it`` flag *and*
  ``ALLOW_LAKE_RESET=1`` in the environment, so neither a stray flag in shell
  history nor an exported variable is enough on its own.
* **Production break-glass.** When ``ENV`` is ``prod``/``production`` (what the
  Helm chart sets), executing additionally needs ``--break-glass`` and a typed
  confirmation (``<operation> <env>``) on an interactive terminal; there is no
  non-interactive way to wipe production.
* **Audit trail.** Every attempt (dry run, refusal, execution, failure) appends
  one JSON line with actor, host, time, argv, environment, targets and outcome
  to ``$LAKE_AUDIT_LOG`` (default ``data/logs/destructive_ops.jsonl``, outside
  the lake so a lake reset cannot remove its own audit record).
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import socket
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.core.constants import LOGS_DIR

logger = logging.getLogger(__name__)

ALLOW_ENV_VAR = "ALLOW_LAKE_RESET"
AUDIT_ENV_VAR = "LAKE_AUDIT_LOG"
CONFIRM_FLAG = "--i-really-mean-it"
BREAK_GLASS_FLAG = "--break-glass"
PRODUCTION_ENVS = frozenset({"prod", "production"})


class DestructiveOpRefused(PermissionError):
    """Raised when a destructive operation is missing a required confirmation."""


def current_env(environ: Mapping[str, str] | None = None) -> str:
    environ = os.environ if environ is None else environ
    return (environ.get("ENV") or "development").strip().lower()


def is_production(environ: Mapping[str, str] | None = None) -> bool:
    return current_env(environ) in PRODUCTION_ENVS


def audit_path(environ: Mapping[str, str] | None = None) -> Path:
    override = (environ or {}).get(AUDIT_ENV_VAR) or os.environ.get(AUDIT_ENV_VAR)
    return Path(override) if override else LOGS_DIR / "destructive_ops.jsonl"


def audit(
    operation: str,
    targets: Sequence[str],
    outcome: str,
    *,
    dry_run: bool,
    details: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Append one audit record; never raises (an unwritable log is logged loudly)."""
    environ = os.environ if environ is None else environ
    try:
        actor = getpass.getuser()
    except Exception:  # no passwd entry in some containers
        actor = environ.get("USER") or "unknown"
    record: dict[str, Any] = {
        "at": datetime.now(UTC).isoformat(),
        "operation": operation,
        "outcome": outcome,
        "dry_run": dry_run,
        "actor": actor,
        "sudo_user": environ.get("SUDO_USER"),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "env": current_env(environ),
        "argv": list(sys.argv),
        "targets": list(targets),
        "details": dict(details or {}),
    }
    path = audit_path(environ)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        logger.error(f"[AUDIT] could not write {path}: {exc}; record follows: {record}")
    log = logger.info if dry_run else logger.warning
    log(f"[AUDIT] {operation} {outcome} by {actor}@{record['host']} env={record['env']} targets={list(targets)}")
    return record


def authorize(
    operation: str,
    targets: Sequence[str],
    *,
    execute: bool,
    i_really_mean_it: bool,
    break_glass: bool = False,
    environ: Mapping[str, str] | None = None,
    input_fn: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> bool:
    """Decide whether a destructive operation may run.

    Returns ``False`` for a dry run (caller reports and exits), ``True`` when
    every confirmation is present. Raises ``DestructiveOpRefused`` naming what
    is missing otherwise. Every outcome is audited.
    """
    environ = os.environ if environ is None else environ
    if not execute:
        audit(operation, targets, "dry_run", dry_run=True, environ=environ)
        return False

    missing = []
    if not i_really_mean_it:
        missing.append(CONFIRM_FLAG)
    if environ.get(ALLOW_ENV_VAR) != "1":
        missing.append(f"{ALLOW_ENV_VAR}=1")
    env = current_env(environ)
    prod = env in PRODUCTION_ENVS
    if prod and not break_glass:
        missing.append(f"{BREAK_GLASS_FLAG} (ENV={env})")
    if missing:
        audit(operation, targets, "refused", dry_run=False, details={"missing": missing}, environ=environ)
        raise DestructiveOpRefused(f"{operation} refused: missing {', '.join(missing)}")

    if prod:
        if interactive is None:
            interactive = sys.stdin.isatty()
        expected = f"{operation} {env}"
        if not interactive:
            audit(operation, targets, "refused", dry_run=False, details={"missing": ["typed confirmation (no TTY)"]}, environ=environ)
            raise DestructiveOpRefused(f"{operation} refused: ENV={env} needs a typed confirmation on an interactive terminal")
        typed = input_fn(f"Production {operation} of {len(targets)} target(s). Type '{expected}' to proceed: ")
        if typed.strip() != expected:
            audit(operation, targets, "refused", dry_run=False, details={"missing": ["typed confirmation mismatch"]}, environ=environ)
            raise DestructiveOpRefused(f"{operation} refused: typed confirmation did not match '{expected}'")

    audit(operation, targets, "authorized", dry_run=False, details={"break_glass": break_glass}, environ=environ)
    return True


def add_guard_arguments(parser: Any) -> None:
    """The standard flags every destructive CLI exposes."""
    parser.add_argument("--execute", action="store_true", help="Actually perform the operation (default is a dry run)")
    parser.add_argument(CONFIRM_FLAG, dest="i_really_mean_it", action="store_true", help=f"Second confirmation; also requires {ALLOW_ENV_VAR}=1")
    parser.add_argument(BREAK_GLASS_FLAG, dest="break_glass", action="store_true", help="Required (with a typed confirmation) when ENV=production")


DRY_RUN_EXIT = 10
REFUSED_EXIT = 3


def main(argv: list[str] | None = None) -> int:
    """Shell/Makefile entry point: ``python -m src.utils.destructive_guard OP TARGET...``.

    Exit 0 = authorized (proceed), 10 = dry run (stop, nothing done), 3 = refused.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m src.utils.destructive_guard", description=main.__doc__)
    parser.add_argument("operation")
    parser.add_argument("targets", nargs="+")
    add_guard_arguments(parser)
    args = parser.parse_args(argv)
    try:
        ok = authorize(args.operation, args.targets, execute=args.execute, i_really_mean_it=args.i_really_mean_it, break_glass=args.break_glass)
    except DestructiveOpRefused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return REFUSED_EXIT
    if not ok:
        print(
            f"DRY RUN: {args.operation} would affect: {' '.join(args.targets)}\n"
            f"To execute: {ALLOW_ENV_VAR}=1 plus --execute {CONFIRM_FLAG}"
            + (f" and {BREAK_GLASS_FLAG}" if is_production() else ""),
            file=sys.stderr,
        )
        return DRY_RUN_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
