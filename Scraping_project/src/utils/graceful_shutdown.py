"""In-process graceful drain for long-running workers (#183, #185, #325).

Not to be confused with ``shutdown.py`` at the repo root, which tears down the
*infrastructure* (Compose stack / Helm release). This module is about one
Python process reacting to SIGTERM/SIGINT:

* The first signal sets a process-wide flag. Continuous stage loops check it
  at batch boundaries: they stop scheduling new work, finish and flush the
  batch in flight (Delta write + queue status), and return, so the process
  exits 0 well inside Kubernetes' ``terminationGracePeriodSeconds`` (120).
* If no drain loop is running (a script that only built a LakehouseManager,
  say), the first signal runs the registered cleanups and exits 0 right away,
  which is what LakehouseManager's own handler used to do.
* A drain that overruns ``WORKER_SHUTDOWN_TIMEOUT`` seconds (default 100) is
  force-exited with status 1; a second signal forces exit immediately
  (status 128 + signum).

Cleanups (e.g. LakehouseManager.shutdown, which drains the async Delta write
queue) are registered with :meth:`GracefulShutdown.add_cleanup`, held weakly
for bound methods, run at most once, newest first, on signal-exit or at
interpreter exit.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import threading
import weakref
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SHUTDOWN_TIMEOUT = 100.0  # < helm terminationGracePeriodSeconds (120)


def shutdown_timeout_seconds(default: float = DEFAULT_SHUTDOWN_TIMEOUT) -> float:
    raw = os.getenv("WORKER_SHUTDOWN_TIMEOUT")
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning(f"Ignoring invalid WORKER_SHUTDOWN_TIMEOUT={raw!r}")
        return default
    return value if value > 0 else default


class GracefulShutdown:
    def __init__(self, *, force_exit: Callable[[int], Any] = os._exit):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._cleanups: list[tuple[str, Callable[[], Callable[[], Any] | None]]] = []
        self._drain_loops = 0
        self._timer: threading.Timer | None = None
        self._force_exit = force_exit
        self.reason: str | None = None
        self.timeout: float = shutdown_timeout_seconds()

    # ------------------------------------------------------------- flag
    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def request(self, reason: str = "requested") -> None:
        with self._lock:
            if self._event.is_set():
                return
            self.reason = reason
            self._event.set()
        logger.info(f"Graceful shutdown requested ({reason})")

    async def sleep(self, seconds: float, step: float = 0.25) -> bool:
        """Sleep up to ``seconds``; returns True (early) once shutdown is requested."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, seconds)
        while not self._event.is_set():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(step, remaining))
        return True

    # ------------------------------------------------------- drain loops
    def begin_drain(self) -> None:
        """Declare a cooperative loop: signals now only set the flag."""
        with self._lock:
            self._drain_loops += 1

    def end_drain(self) -> None:
        with self._lock:
            self._drain_loops = max(0, self._drain_loops - 1)
            timer, self._timer = (self._timer, None) if self._drain_loops == 0 else (None, self._timer)
        if timer is not None:
            timer.cancel()

    @property
    def draining(self) -> bool:
        return self._drain_loops > 0

    # ---------------------------------------------------------- cleanups
    def add_cleanup(self, callback: Callable[[], Any], name: str | None = None) -> None:
        """Register ``callback``; bound methods are held weakly (no leak per instance)."""
        ref: Callable[[], Callable[[], Any] | None]
        if hasattr(callback, "__self__") and hasattr(callback, "__func__"):
            ref = weakref.WeakMethod(callback)
        else:

            def ref() -> Callable[[], Any] | None:
                return callback

        with self._lock:
            self._cleanups.append((name or getattr(callback, "__qualname__", repr(callback)), ref))

    def run_cleanups(self) -> None:
        with self._lock:
            cleanups, self._cleanups = self._cleanups, []
        for name, ref in reversed(cleanups):
            callback = ref()
            if callback is None:
                continue
            try:
                callback()
            except Exception as e:  # one failing cleanup must not skip the rest
                logger.error(f"Shutdown cleanup {name} failed: {e}", exc_info=True)

    # ----------------------------------------------------------- signals
    def handle_signal(self, signum: int, frame: Any = None) -> None:
        name = signal.Signals(signum).name
        if self.requested:
            logger.warning(f"{name} received again; forcing exit")
            raise SystemExit(128 + signum)
        self.request(name)
        if not self.draining:
            # Nobody will drain cooperatively: flush now and leave.
            self.run_cleanups()
            raise SystemExit(0)
        logger.info(f"{name}: finishing the batch in flight, then exiting (limit {self.timeout:.0f}s)")
        if self.timeout > 0:
            timer = threading.Timer(self.timeout, self._overrun)
            timer.daemon = True
            with self._lock:
                self._timer = timer
            timer.start()

    def _overrun(self) -> None:
        logger.error(f"Graceful drain exceeded {self.timeout:.0f}s; forcing exit")
        self._force_exit(1)


_shutdown: GracefulShutdown | None = None
_installed: set[int] = set()
_atexit_registered = False


def get_shutdown() -> GracefulShutdown:
    global _shutdown
    if _shutdown is None:
        _shutdown = GracefulShutdown()
    return _shutdown


def shutdown_requested() -> bool:
    return _shutdown is not None and _shutdown.requested


_DEFAULT_HANDLERS = (signal.SIG_DFL, signal.default_int_handler, None)


def install_signal_handlers(
    shutdown: GracefulShutdown | None = None,
    signals: tuple[int, ...] = (signal.SIGTERM, signal.SIGINT),
    *,
    override: bool = False,
) -> bool:
    """Route ``signals`` to ``shutdown`` (default: the process one). Main thread only.

    Without ``override``, a handler someone else installed (Scrapy's
    CrawlerProcess graceful stop, ``SIG_IGN`` under nohup, ...) is left alone;
    the registered cleanups still run at interpreter exit. Returns True if at
    least one handler is ours afterwards.
    """
    global _atexit_registered
    shutdown = shutdown or get_shutdown()
    if not _atexit_registered and shutdown is _shutdown:
        atexit.register(lambda: _shutdown.run_cleanups() if _shutdown is not None else None)
        _atexit_registered = True
    if threading.current_thread() is not threading.main_thread():
        logger.debug("Not on the main thread; signal handlers not installed")
        return False
    ours = False
    for signum in signals:
        current = signal.getsignal(signum)
        if current == shutdown.handle_signal:
            ours = True
            continue
        if not override and current not in _DEFAULT_HANDLERS:
            logger.debug(f"Keeping existing {signal.Signals(signum).name} handler {current!r}")
            continue
        signal.signal(signum, shutdown.handle_signal)
        _installed.add(signum)
        ours = True
    return ours


def worker_shutdown(stage: str) -> GracefulShutdown:
    """Install handlers and mark this process as a cooperative drain loop."""
    shutdown = get_shutdown()
    install_signal_handlers(shutdown, override=True)
    shutdown.begin_drain()
    logger.info(f"[{stage}] SIGTERM/SIGINT drain enabled (WORKER_SHUTDOWN_TIMEOUT={shutdown.timeout:.0f}s)")
    return shutdown


async def run_drain_loop(
    stage: str,
    run_once: Callable[[], Awaitable[Any]],
    *,
    idle_seconds: float,
    error_seconds: float,
    shutdown: GracefulShutdown | None = None,
) -> None:
    """Continuous stage loop: ``run_once()``, idle, repeat, until shutdown.

    ``run_once`` is expected to check :func:`shutdown_requested` at its batch
    boundaries; this loop never starts another run after the flag is set and
    cuts the idle/backoff sleep short. Returns normally (exit code 0).
    """
    if shutdown is None:
        shutdown = worker_shutdown(stage)
    else:
        shutdown.begin_drain()
    try:
        while not shutdown.requested:
            try:
                await run_once()
            except KeyboardInterrupt:  # handlers not installed (non-main thread)
                logger.info(f"[{stage}] Worker shutting down...")
                break
            except Exception as e:
                logger.error(f"[{stage}] Error in worker loop: {e}")
                if await shutdown.sleep(error_seconds):
                    break
                continue
            if shutdown.requested:
                break
            logger.info(f"[{stage}] Waiting {idle_seconds:.0f} seconds before next check...")
            if await shutdown.sleep(idle_seconds):
                break
    finally:
        shutdown.end_drain()
    logger.info(f"[{stage}] Worker stopped ({shutdown.reason or 'loop ended'})")


def reset_for_tests() -> None:
    """Drop the process singleton and restore default signal handlers."""
    global _shutdown
    if _shutdown is not None and _shutdown._timer is not None:
        _shutdown._timer.cancel()
    _shutdown = None
    if threading.current_thread() is threading.main_thread():
        for signum in list(_installed):
            signal.signal(signum, signal.SIG_DFL if signum != signal.SIGINT else signal.default_int_handler)
    _installed.clear()
