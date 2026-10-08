"""
Performance profiling utilities for identifying bottlenecks.

Phase 8: Performance optimization through profiling and monitoring.
"""

import time
import logging
import asyncio
from typing import Optional, Dict, Any, Awaitable, Callable, TypeVar, cast
from functools import wraps
from contextlib import asynccontextmanager
from collections import defaultdict

logger = logging.getLogger(__name__)

T = TypeVar('T')
# Signature-preserving decorator type (works for sync and async callables).
F = TypeVar('F', bound=Callable[..., Any])


class PerformanceTimer:
    """Context manager for timing code blocks."""

    def __init__(self, name: str, log_threshold_ms: Optional[float] = None):
        self.name = name
        self.log_threshold_ms = log_threshold_ms
        self.start_time: Optional[float] = None
        self.duration_ms: Optional[float] = None

    def __enter__(self):
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        end_time = time.perf_counter()
        self.duration_ms = (end_time - self.start_time) * 1000

        if self.log_threshold_ms is None or self.duration_ms >= self.log_threshold_ms:
            logger.info(f"{self.name} took {self.duration_ms:.2f}ms")


@asynccontextmanager
async def async_timer(name: str, log_threshold_ms: Optional[float] = None):
    """Async context manager for timing async code blocks."""
    start_time = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start_time) * 1000
        if log_threshold_ms is None or duration_ms >= log_threshold_ms:
            logger.info(f"{name} took {duration_ms:.2f}ms")


class FunctionProfiler:
    """
    Profiler for tracking function execution times and call counts.
    
    Tracks min/max/avg execution times and call frequency.
    """

    def __init__(self):
        self.stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "call_count": 0,
            "total_time_ms": 0.0,
            "min_time_ms": float('inf'),
            "max_time_ms": 0.0,
            "errors": 0
        })
        self._lock = asyncio.Lock()

    async def record(self, func_name: str, duration_ms: float, error: bool = False):
        """Record function execution."""
        async with self._lock:
            stat = self.stats[func_name]
            stat["call_count"] += 1
            stat["total_time_ms"] += duration_ms
            stat["min_time_ms"] = min(stat["min_time_ms"], duration_ms)
            stat["max_time_ms"] = max(stat["max_time_ms"], duration_ms)
            if error:
                stat["errors"] += 1

    def get_stats(self, func_name: Optional[str] = None) -> Dict[str, Any]:
        """Get profiling statistics."""
        if func_name:
            stat = self.stats.get(func_name, {})
            if stat and stat["call_count"] > 0:
                return {
                    **stat,
                    "avg_time_ms": stat["total_time_ms"] / stat["call_count"],
                    "error_rate": stat["errors"] / stat["call_count"]
                }
            return {}

        # Return all stats
        result = {}
        for name, stat in self.stats.items():
            if stat["call_count"] > 0:
                result[name] = {
                    **stat,
                    "avg_time_ms": stat["total_time_ms"] / stat["call_count"],
                    "error_rate": stat["errors"] / stat["call_count"]
                }
        return result

    def reset(self):
        """Reset all statistics."""
        self.stats.clear()


# Global profiler instance
_global_profiler = FunctionProfiler()


def profile(func: F) -> F:
    """
    Decorator to profile function execution.
    
    Example:
        @profile
        async def expensive_function():
            await asyncio.sleep(1)
    """
    @wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.perf_counter()
        error = False
        
        try:
            result = await func(*args, **kwargs)
            return result
        except Exception:
            error = True
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            await _global_profiler.record(
                func.__name__,
                duration_ms,
                error=error
            )

    @wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        start_time = time.perf_counter()
        error = False
        
        try:
            result = func(*args, **kwargs)
            return result
        except Exception:
            error = True
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            # For sync functions, use asyncio.create_task if possible
            # Otherwise just update stats synchronously
            stat = _global_profiler.stats[func.__name__]
            stat["call_count"] += 1
            stat["total_time_ms"] += duration_ms
            stat["min_time_ms"] = min(stat["min_time_ms"], duration_ms)
            stat["max_time_ms"] = max(stat["max_time_ms"], duration_ms)
            if error:
                stat["errors"] += 1

    if asyncio.iscoroutinefunction(func):
        return cast(F, async_wrapper)
    return cast(F, sync_wrapper)


def get_profiler() -> FunctionProfiler:
    """Get global profiler instance."""
    return _global_profiler


def log_slow_queries(threshold_ms: float = 1000):
    """
    Decorator to log slow database queries.
    
    Args:
        threshold_ms: Log queries slower than this threshold
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            async with async_timer(f"Query: {func.__name__}", log_threshold_ms=threshold_ms):
                return await func(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# One-run cProfile capture (#465): `python cli.py --profile <command> ...`
# ---------------------------------------------------------------------------

DEFAULT_PROFILE_DIR = "data/exports/profiles"


def run_profiled(
    fn: Callable[[], Any],
    label: str,
    out_dir: str = DEFAULT_PROFILE_DIR,
    top: int = 30,
) -> Dict[str, Any]:
    """Run ``fn`` under cProfile and write ``profile_<label>_<ts>.pstats`` + ``.json``.

    The JSON holds wall time and the ``top`` functions by cumulative time; the
    .pstats opens in ``python -m pstats`` or snakeviz. Artifacts are written
    even if ``fn`` raises (the exception is re-raised; SystemExit included).
    Nothing is imported or hooked unless this is called, so profiling off = zero overhead.
    """
    import cProfile
    import json
    import pstats
    import re
    from datetime import datetime, timezone
    from pathlib import Path

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label) or "run"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = out / f"profile_{safe}_{stamp}"
    prof = cProfile.Profile()
    start = time.perf_counter()
    outcome = "ok"
    result: Dict[str, Any] = {}
    try:
        prof.enable()
        try:
            fn()
        finally:
            prof.disable()
    except BaseException as exc:
        outcome = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        wall = time.perf_counter() - start
        prof.dump_stats(str(base) + ".pstats")
        stats = pstats.Stats(prof)
        rows = []
        for (filename, line, func), (cc, nc, tt, ct, _callers) in stats.stats.items():  # type: ignore[attr-defined]
            rows.append({
                "function": f"{Path(filename).name}:{line}({func})",
                "calls": nc,
                "primitive_calls": cc,
                "tottime_s": round(tt, 6),
                "cumtime_s": round(ct, 6),
            })
        rows.sort(key=lambda r: r["cumtime_s"], reverse=True)
        result = {
            "label": label,
            "started_utc": stamp,
            "wall_time_s": round(wall, 6),
            "outcome": outcome,
            "pstats": str(base) + ".pstats",
            "top_cumulative": rows[:top],
        }
        Path(str(base) + ".json").write_text(json.dumps(result, indent=2))
        logger.info(f"Profile written: {base}.json / .pstats (wall {wall:.2f}s)")
    return result
