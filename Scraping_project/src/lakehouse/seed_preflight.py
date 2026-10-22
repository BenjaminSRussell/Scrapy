"""Preflight for large seed loads (#453).

A big seed CSV can add far more URLs than the Redis queue should take in one
go. Before the write, show how many URLs are new, the current Redis queue
depth, and the depth after loading everything. Above ``seed_load.warn_threshold``
the operator loads everything, loads a random sample, or stops.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from typing import Any, TypeVar

from src.lakehouse.seed_manager import read_seed_queue_depth
from src.utils.redis import queue_watermark_settings

logger = logging.getLogger(__name__)

DEFAULT_WARN_THRESHOLD = 10_000
DEFAULT_SAMPLE_SIZE = 1_000

T = TypeVar("T")


def preflight_settings(config: Any = None) -> dict[str, int]:
    """``seed_load.warn_threshold`` and ``seed_load.sample_size`` from config.yml.

    A missing, unparsable, or non-positive value uses the default.
    """
    if config is None:
        try:
            from src.core.config import get_config

            config = get_config()
        except Exception:
            config = None
    values: dict[str, int] = {}
    for key, default in (("warn_threshold", DEFAULT_WARN_THRESHOLD), ("sample_size", DEFAULT_SAMPLE_SIZE)):
        raw = None
        if config is not None:
            try:
                raw = config.get(f"seed_load.{key}")
            except Exception:
                raw = None
        try:
            value = int(raw) if raw is not None else default
        except (TypeError, ValueError):
            value = default
        values[key] = value if value > 0 else default
    return values


def select_seeds(records: list[T], count: int | None) -> list[T]:
    """Random subset of ``count`` records, or all of them when ``count`` is None or not smaller."""
    if count is None or count >= len(records):
        return records
    return random.sample(records, count)


def confirm_seed_load(
    count: int,
    *,
    sample: int | None = None,
    assume_yes: bool = False,
    interactive: bool = False,
    config: Any = None,
    input_fn: Callable[[str], str] = input,
) -> int | None:
    """How many of ``count`` new seed URLs to write: all, a sample, or None to stop.

    At or below the threshold this returns ``count`` with no prompt. An explicit
    ``sample`` is the operator's choice and skips the prompt. Above the threshold
    without ``assume_yes`` a non-interactive run stops (None) and an interactive
    run asks.
    """
    settings = preflight_settings(config)
    threshold = settings["warn_threshold"]
    if sample is not None:
        return min(sample, count)
    if count <= threshold:
        return count

    sample_size = min(settings["sample_size"], count)
    depth = read_seed_queue_depth()
    high = int(queue_watermark_settings()["high"])
    logger.warning(
        "Seed load adds %d new URLs, above the %d-URL warning threshold (seed_load.warn_threshold).",
        count,
        threshold,
    )
    if depth is None:
        logger.warning("Redis queue depth is unknown (Redis unreachable); seed enqueue may be skipped.")
    else:
        logger.warning(
            "Redis queue depth now: %d; after loading all %d: %d (high watermark %d).",
            depth,
            count,
            depth + count,
            high,
        )
        if depth + count > high:
            logger.warning("Loading all of them puts the queue above its high watermark; seeds past it are skipped.")

    if assume_yes:
        logger.warning("--yes given: loading all %d URLs.", count)
        return count
    if not interactive:
        logger.error(
            "Refusing to load %d URLs without confirmation. Re-run with --yes, or --sample %d.",
            count,
            sample_size,
        )
        return None
    answer = input_fn(f"Load [a]ll {count}, [s]ample {sample_size}, or [q]uit? ").strip().lower()
    if answer in ("a", "all"):
        return count
    if answer in ("s", "sample"):
        return sample_size
    logger.info("Seed load stopped; nothing was written.")
    return None
