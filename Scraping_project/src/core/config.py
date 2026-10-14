"""
Global configuration management.

Consolidated from:
- src/common/config.py
- src/common/config_manager.py

Provides unified configuration access with YAML file support and sensible defaults.
"""

import copy
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge

    CONFIG_GENERATION = Gauge(
        "scrapy_config_generation",
        "Generation number of the live config snapshot (bumps on every successful load/reload/set)",
    )
    CONFIG_RELOAD_FAILURES = Counter(
        "scrapy_config_reload_failures_total",
        "Config reloads rejected because the file could not be parsed; the previous snapshot stayed live",
    )
except Exception:  # prometheus_client missing or metric already registered
    CONFIG_GENERATION = None
    CONFIG_RELOAD_FAILURES = None


def _lookup(data: dict, key: str, default: Any) -> Any:
    value: Any = data
    for k in key.split('.'):
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return default
    if value is None:
        return default
    # Containers are copied so a caller can never mutate a shared snapshot.
    return copy.deepcopy(value) if isinstance(value, (dict, list)) else value


def _default_redis_section() -> dict:
    """Redis defaults from the #511 env contract (REDIS_HOST/PORT/DB, REDIS_URL fallback)."""
    from src.utils.redis_env import redis_settings

    env = redis_settings()
    return {"host": env.host, "port": env.port, "db": env.db}


@dataclass(frozen=True)
class ConfigSnapshot:
    """One immutable generation of configuration (#590).

    Every value read from a snapshot comes from the same load, so reading
    several related keys (say ``redis.host`` and ``redis.password``) from one
    snapshot can never mix an old value with a new one, even while another
    thread reloads.
    """

    generation: int
    data: dict

    def get(self, key: str, default: Any = None) -> Any:
        return _lookup(self.data, key, default)

    def get_section(self, section: str) -> dict:
        section_config = self.data.get(section, {})
        return copy.deepcopy(section_config) if isinstance(section_config, dict) else {}


class Config:
    """Global configuration manager with singleton pattern.

    Reload semantics (#590):

    * The live configuration is an immutable :class:`ConfigSnapshot`. ``load()``,
      ``reload()`` and ``set()`` build a complete new snapshot and swap it in
      with one reference assignment under a lock. A reader sees the old
      snapshot or the new one, never a mix.
    * Each swap bumps ``generation``, which is exported as the
      ``scrapy_config_generation`` gauge.
    * Reads that must agree with each other should use one ``snapshot()``.
      Separate ``get()`` calls may straddle a reload.
    * A reload that cannot parse the file (for example, one that's
      half-written) keeps the previous snapshot live, returns False, and
      counts ``scrapy_config_reload_failures_total``. Only the very first load
      falls back to defaults.
    * Values handed out by ``get()``/``get_section()``/``get_raw_config()``
      are copies; mutating them doesn't change the live config. Use ``set()``.
    """

    _instance: Optional['Config'] = None
    _instance_lock = threading.Lock()

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize configuration.

        Args:
            config_path: Path to YAML config file. Defaults to project_root/config.yml
        """
        if config_path is None:
            project_root = Path(__file__).parent.parent.parent
            config_path = project_root / "config.yml"

        self.config_path = Path(config_path)
        self._lock = threading.RLock()
        self._snapshot: Optional[ConfigSnapshot] = None
        self.load()

    # ------------------------------------------------------------ snapshots
    def snapshot(self) -> ConfigSnapshot:
        """The current generation, for reads that must be consistent."""
        snap = self._snapshot
        assert snap is not None
        return snap

    @property
    def generation(self) -> int:
        return self.snapshot().generation

    @property
    def _config(self) -> dict:
        """Backward-compatible view of the live data (a copy)."""
        return copy.deepcopy(self.snapshot().data)

    def _swap(self, data: dict) -> ConfigSnapshot:
        with self._lock:
            previous = self._snapshot
            snap = ConfigSnapshot(
                generation=(previous.generation + 1) if previous else 1,
                data=data,
            )
            self._snapshot = snap
        if CONFIG_GENERATION is not None:
            CONFIG_GENERATION.set(snap.generation)
        return snap

    # ---------------------------------------------------------------- load
    def _read_file(self) -> dict:
        with open(self.config_path) as f:
            loaded = yaml.safe_load(f)
        if loaded is None:
            return {}
        if not isinstance(loaded, dict):
            raise ValueError(f"top level of {self.config_path} is {type(loaded).__name__}, not a mapping")
        return loaded

    def load(self) -> bool:
        """Load configuration from YAML file and swap it in atomically.

        Returns True when a new snapshot went live. On a parse failure after
        the first load, the previous snapshot is kept and False is returned.
        """
        # One loader at a time, so two concurrent reloads can't interleave;
        # readers never take this lock.
        with self._lock:
            if not self.config_path.exists():
                if self._snapshot is not None:
                    logger.error(
                        "Config file %s disappeared; keeping generation %d",
                        self.config_path, self._snapshot.generation,
                    )
                    if CONFIG_RELOAD_FAILURES is not None:
                        CONFIG_RELOAD_FAILURES.inc()
                    return False
                logger.warning(f"Config file not found: {self.config_path}, using defaults")
                self._swap(self._default_config())
                return True

            try:
                data = self._read_file()
            except Exception as e:
                if self._snapshot is not None:
                    logger.error(
                        "Config reload from %s failed (%s); keeping generation %d",
                        self.config_path, e, self._snapshot.generation,
                    )
                    if CONFIG_RELOAD_FAILURES is not None:
                        CONFIG_RELOAD_FAILURES.inc()
                    return False
                logger.error(f"Failed to load config: {e}, using defaults")
                self._swap(self._default_config())
                return True

            snap = self._swap(data)
            logger.info(f"Configuration loaded from {self.config_path} (generation {snap.generation})")
            return True

    def _default_config(self) -> dict:
        """Default configuration."""
        return {
            "redis": _default_redis_section(),
            "delta_lake": {
                "base_path": "./data/delta_lake"
            },
            "stages": {
                "stage1": {
                    "url_limit": 100,
                    "concurrent_requests": 512
                },
                "stage2": {
                    "concurrent": 50,
                    "poll_interval": 3
                },
                "stage3": {
                    "concurrent": 20,
                    "poll_interval": 5
                },
                "stage4": {
                    "enabled": True
                }
            }
        }

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get config value by dot notation key.

        Args:
            key: Dot-notation key (e.g., "redis.host")
            default: Default value if key not found

        Returns:
            Config value or default

        Example:
            config = get_config()
            redis_host = config.get("redis.host", "localhost")
        """
        return self.snapshot().get(key, default)

    def set(self, key: str, value: Any) -> None:
        """
        Set config value by dot notation key.

        Copy-on-write: a new snapshot is built and swapped in, so readers
        never see a partially applied change.

        Example:
            config = get_config()
            config.set("redis.host", "redis.example.com")
        """
        keys = key.split('.')
        with self._lock:
            data = copy.deepcopy(self.snapshot().data)
            node = data
            for k in keys[:-1]:
                if not isinstance(node.get(k), dict):
                    node[k] = {}
                node = node[k]
            node[keys[-1]] = value
            self._swap(data)

    def get_section(self, section: str) -> dict:
        """
        Get entire config section (a copy).

        Example:
            config = get_config()
            redis_config = config.get_section("redis")
        """
        return self.snapshot().get_section(section)

    def reload(self) -> bool:
        """Reload configuration from file. See the class docstring for semantics."""
        return self.load()

    @classmethod
    def get_instance(cls, config_path: Optional[Path] = None) -> 'Config':
        """
        Get singleton instance of Config.

        Args:
            config_path: Optional path to config file

        Returns:
            Config instance
        """
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(config_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (useful for testing)."""
        with cls._instance_lock:
            cls._instance = None

    def get_raw_config(self) -> dict:
        """Get raw config dictionary (a deep copy of the live snapshot)."""
        return copy.deepcopy(self.snapshot().data)


# Global singleton
_config_instance: Optional[Config] = None
_config_instance_lock = threading.Lock()


def get_config(config_path: Optional[Path] = None) -> Config:
    """
    Get global config instance.

    This is the primary way to access configuration throughout the pipeline.

    Args:
        config_path: Optional path to config file

    Returns:
        Config instance

    Example:
        from src.core.config import get_config

        config = get_config()
        redis_host = config.get("redis.host")
        stage2_workers = config.get("stages.stage2.concurrent", 50)
    """
    global _config_instance
    if _config_instance is None:
        with _config_instance_lock:
            if _config_instance is None:
                _config_instance = Config(config_path)
    return _config_instance


def reset_config():
    """Reset global config instance (useful for testing)."""
    global _config_instance
    with _config_instance_lock:
        _config_instance = None
    Config.reset_instance()


def _positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def stage_worker_settings(
    stage: int,
    default_concurrent: int,
    default_batch_size: int,
    config: Optional[Config] = None,
) -> tuple[int, int]:
    """Resolve (max_concurrent, batch_size) for a continuous stage worker.

    Precedence: env ``STAGE{N}_CONCURRENT`` / ``STAGE{N}_BATCH_SIZE`` >
    config.yml ``stage{N}.max_workers`` / ``stage{N}.batch_size`` >
    legacy ``stages.stage{N}.concurrent`` > the given defaults.
    Invalid or non-positive values fall through to the next source.
    """
    cfg = config if config is not None else get_config()
    concurrent = (
        _positive_int(os.getenv(f"STAGE{stage}_CONCURRENT"))
        or _positive_int(cfg.get(f"stage{stage}.max_workers"))
        or _positive_int(cfg.get(f"stages.stage{stage}.concurrent"))
        or default_concurrent
    )
    batch_size = (
        _positive_int(os.getenv(f"STAGE{stage}_BATCH_SIZE"))
        or _positive_int(cfg.get(f"stage{stage}.batch_size"))
        or _positive_int(cfg.get(f"stages.stage{stage}.batch_size"))
        or default_batch_size
    )
    return concurrent, batch_size


@dataclass(frozen=True)
class Stage2Thresholds:
    """Stage 2 page-quality gates (#329)."""

    min_word_count: int
    min_text_to_html_ratio: float
    massive_doc_threshold: int


DEFAULT_STAGE2_THRESHOLDS = Stage2Thresholds(min_word_count=50, min_text_to_html_ratio=0.1, massive_doc_threshold=50000)


def _non_negative_int(value: Any) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _ratio(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if 0.0 <= parsed <= 1.0 else None


def stage2_quality_thresholds(config: Optional[Config] = None) -> Stage2Thresholds:
    """Resolve Stage 2 quality thresholds (#329).

    Precedence per key: env ``STAGE2_MIN_WORD_COUNT`` / ``STAGE2_MIN_TEXT_TO_HTML_RATIO``
    / ``STAGE2_MASSIVE_DOC_THRESHOLD`` > config.yml ``stage2.<key>`` > legacy
    ``stages.stage2.<key>`` > ``DEFAULT_STAGE2_THRESHOLDS`` (today's values).
    Invalid values (non-numeric, negative, ratio outside [0, 1], massive <= 0)
    fall through to the next source.
    """
    cfg = config if config is not None else get_config()

    def pick(key: str, parse: Any, default: Any) -> Any:
        for raw in (os.getenv(f"STAGE2_{key.upper()}"), cfg.get(f"stage2.{key}"), cfg.get(f"stages.stage2.{key}")):
            if raw is None or raw == "":
                continue
            parsed = parse(raw)
            if parsed is not None:
                return parsed
        return default

    d = DEFAULT_STAGE2_THRESHOLDS
    return Stage2Thresholds(
        min_word_count=pick("min_word_count", _non_negative_int, d.min_word_count),
        min_text_to_html_ratio=pick("min_text_to_html_ratio", _ratio, d.min_text_to_html_ratio),
        massive_doc_threshold=pick("massive_doc_threshold", _positive_int, d.massive_doc_threshold),
    )
