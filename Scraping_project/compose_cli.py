"""Which Docker Compose CLI to run (#342).

Many Linux installs only ship the v2 plugin (``docker compose``); older ones only
the standalone ``docker-compose``. ``start.py`` / ``shutdown.py`` call
:func:`compose_cmd` instead of hard-coding either one. Shell scripts use the same
rule via ``scripts/compose_lib.sh`` and the Makefile via ``COMPOSE ?=``.

Resolution order:

1. ``COMPOSE_CMD`` env var, e.g. ``COMPOSE_CMD="docker compose"`` (explicit wins).
2. ``docker-compose`` on PATH (keeps existing setups unchanged).
3. ``docker compose`` when ``docker`` is on PATH and the plugin answers
   ``docker compose version``. With ``probe=False`` (dry runs, which must not
   execute anything) the plugin is assumed when ``docker`` exists.

Kept dependency-free (stdlib only): ``start.py --dry-run`` must not import Redis
or the pipeline.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess

LEGACY: tuple[str, ...] = ("docker-compose",)
PLUGIN: tuple[str, ...] = ("docker", "compose")
MISSING_HINT = "docker-compose (or the `docker compose` plugin)"


def _plugin_works() -> bool:
    try:
        result = subprocess.run(
            [*PLUGIN, "version"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def compose_cmd(*, probe: bool = True) -> tuple[str, ...]:
    """The Compose command prefix to run, e.g. ``("docker", "compose")``.

    Falls back to ``("docker-compose",)`` when nothing is found, so callers'
    error messages name the legacy binary; use :func:`compose_available` to check.
    """
    override = os.getenv("COMPOSE_CMD", "").strip()
    if override:
        return tuple(shlex.split(override))
    if shutil.which("docker-compose"):
        return LEGACY
    if shutil.which("docker") and (not probe or _plugin_works()):
        return PLUGIN
    return LEGACY


def compose_available(*, probe: bool = True) -> bool:
    """True when the command :func:`compose_cmd` picked is actually installed."""
    return shutil.which(compose_cmd(probe=probe)[0]) is not None


def compose_str(*, probe: bool = True) -> str:
    """For printed hints: ``"docker compose"`` or ``"docker-compose"``."""
    return " ".join(compose_cmd(probe=probe))
