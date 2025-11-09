"""
Performance profiling utilities for identifying bottlenecks.

Phase 8: Performance optimization through profiling and monitoring.
"""

import time
import logging
import asyncio
from typing import Optional, Dict, Any, Callable, TypeVar
from functools import wraps
from contextlib import contextmanager, asynccontextmanager
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger(__name__)

T = TypeVar('T')


class PerformanceTimer:
    """Context manager for timing code blocks."""

    def __init__(self, name: str, log_threshold_ms: Optional[float] = None):
        self.name = name
        self.log_threshold_ms = log_threshold_ms
        self.start_time: Optional<float> = None
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


def profile(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to profile function execution.
    
    Example:
        @profile
        async def expensive_function():
            await asyncio.sleep(1)
    """
    @wraps(func)
    async def async_wrapper(*args, **kwargs) -> T:
        start_time = time.perf_counter()
        error = False
        
        try:
            result = await func(*args, **kwargs)
            return result
        except Exception as e:
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
    def sync_wrapper(*args, **kwargs) -> T:
        start_time = time.perf_counter()
        error = False
        
        try:
            result = func(*args, **kwargs)
            return result
        except Exception as e:
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
        return async_wrapper
    return sync_wrapper


def get_profiler() -> FunctionProfiler:
    """Get global profiler instance."""
    return _global_profiler


def log_slow_queries(threshold_ms: float = 1000):
    """
    Decorator to log slow database queries.
    
    Args:
        threshold_ms: Log queries slower than this threshold
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            async with async_timer(f"Query: {func.__name__}", log_threshold_ms=threshold_ms):
                return await func(*args, **kwargs)
        return wrapper
    return decorator
