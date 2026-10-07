"""Container health probes that need nothing beyond the Python stdlib (#181).

The worker image is ``python:3.11-slim``, which has no ``procps``, so the
``pgrep -f ...`` probes previously used in the Helm chart exited 127 (and,
run through ``/bin/sh -c``, would have matched the probe's own shell anyway).

Usage (exit code 0 = healthy, 1 = unhealthy)::

    python -m src.utils.probe alive <cmdline-substring>
    python -m src.utils.probe ready-redis            # REDIS_HOST / REDIS_PORT
    python -m src.utils.probe ready-tcp <host> <port>
"""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

PROC = Path("/proc")


def _ancestors(pid: int, proc: Path = PROC) -> set[int]:
    """This process and its parents (so the probe never matches itself or its shell)."""
    seen = {pid}
    while pid > 1:
        try:
            stat = (proc / str(pid) / "stat").read_text()
            pid = int(stat.rsplit(")", 1)[1].split()[1])  # field 4: ppid
        except (OSError, ValueError, IndexError):
            break
        seen.add(pid)
    return seen


def process_alive(pattern: str, proc: Path = PROC) -> bool:
    """True if some other process's command line contains ``pattern``."""
    needle = pattern.encode()
    skip = _ancestors(os.getpid(), proc)
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) in skip:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
        except OSError:
            continue
        if needle in cmdline:
            return True
    return False


def tcp_ready(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def redis_ready(timeout: float = 3.0) -> bool:
    """Redis answers PING (+PONG, or -NOAUTH which still proves it is up)."""
    host = os.getenv("REDIS_HOST", "localhost")
    port = int(os.getenv("REDIS_PORT", "6379"))
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"PING\r\n")
            reply = sock.recv(64)
    except OSError:
        return False
    return reply.startswith(b"+PONG") or reply.startswith(b"-NOAUTH")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) == 2 and args[0] == "alive":
        ok = process_alive(args[1])
    elif args == ["ready-redis"]:
        ok = redis_ready()
    elif len(args) == 3 and args[0] == "ready-tcp":
        ok = tcp_ready(args[1], int(args[2]))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
