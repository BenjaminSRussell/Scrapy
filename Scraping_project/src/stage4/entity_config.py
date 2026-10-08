"""Loadable, validated config for Stage 4 entity summarization (#483).

Sources, first match wins:
1. an explicit ``path`` argument;
2. ``$STAGE4_ENTITY_CONFIG`` (a YAML file);
3. the ``entity_summarization:`` block of config.yml (``src.core.config``);
4. the defaults below.

A YAML file may hold the block under ``entity_summarization:`` (as
``config/entity_summarization.example.yml`` does) or be the bare mapping.
Invalid YAML, unknown keys (typos) and out-of-range values raise
``EntityConfigError`` naming the source, so a bad config fails at startup
instead of deep inside a model call.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

ENV_VAR = "STAGE4_ENTITY_CONFIG"
EXAMPLE_PATH = Path(__file__).resolve().parents[2] / "config" / "entity_summarization.example.yml"
INPUT_SOURCES = ("delta", "kafka")
WRITE_MODES = ("append",)  # one row per entity summary; overwrite would keep only the last entity
CITATION_STYLES = ("inline",)
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class EntityConfigError(ValueError):
    """Invalid entity summarization configuration."""


@dataclass(frozen=True)
class EntitySummarizationConfig:
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    summarization_model: str = "facebook/bart-large-cnn"
    similarity_threshold: float = 0.85
    min_fact_length: int = 20
    max_fact_length: int = 500
    summary_max_length: int = 300
    summary_min_length: int = 100
    batch_size: int = 100
    device: int = -1
    delta_table_name: str = "entity_summaries"
    delta_write_mode: str = "append"
    input_source: str = "delta"
    delta_input_table: str = "stage3_analytics"
    kafka_input_topic: str = "final_categorized"
    kafka_consumer_group: str = "entity-worker-group"
    enable_date_prefixes: bool = True
    date_format: str = "%Y-%m-%d"
    enable_citations: bool = True
    citation_style: str = "inline"
    log_level: str = "INFO"
    verbose: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_FIELDS = {f.name: f for f in fields(EntitySummarizationConfig)}


def _coerce(name: str, value: Any, source: str) -> Any:
    expected = _FIELDS[name].type
    bad = EntityConfigError(f"{source}: entity_summarization.{name} must be {expected}, got {value!r}")
    if expected == "bool":
        if not isinstance(value, bool):
            raise bad
        return value
    if expected == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad
        return value
    if expected == "float":
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise bad
        return float(value)
    if not isinstance(value, str) or not value.strip():
        raise bad
    return value.strip()


def _validate(cfg: EntitySummarizationConfig, source: str) -> None:
    def fail(msg: str) -> None:
        raise EntityConfigError(f"{source}: entity_summarization.{msg}")

    if not 0.0 <= cfg.similarity_threshold <= 1.0:
        fail(f"similarity_threshold must be within [0, 1], got {cfg.similarity_threshold}")
    if cfg.min_fact_length < 1:
        fail(f"min_fact_length must be >= 1, got {cfg.min_fact_length}")
    if cfg.max_fact_length <= cfg.min_fact_length:
        fail(f"max_fact_length ({cfg.max_fact_length}) must exceed min_fact_length ({cfg.min_fact_length})")
    if cfg.summary_min_length < 1:
        fail(f"summary_min_length must be >= 1, got {cfg.summary_min_length}")
    if cfg.summary_max_length <= cfg.summary_min_length:
        fail(
            f"summary_max_length ({cfg.summary_max_length}) must exceed "
            f"summary_min_length ({cfg.summary_min_length})"
        )
    if cfg.batch_size < 1:
        fail(f"batch_size must be >= 1, got {cfg.batch_size}")
    if cfg.device < -1:
        fail(f"device must be -1 (CPU) or a GPU index >= 0, got {cfg.device}")
    for name, allowed in (
        ("input_source", INPUT_SOURCES),
        ("delta_write_mode", WRITE_MODES),
        ("citation_style", CITATION_STYLES),
    ):
        if getattr(cfg, name) not in allowed:
            fail(f"{name} must be one of {allowed}, got {getattr(cfg, name)!r}")
    if cfg.log_level.upper() not in LOG_LEVELS:
        fail(f"log_level must be one of {LOG_LEVELS}, got {cfg.log_level!r}")
    try:
        rendered = datetime(2024, 1, 31).strftime(cfg.date_format)
    except ValueError as e:
        fail(f"date_format {cfg.date_format!r} is invalid: {e}")
    if rendered == cfg.date_format:
        fail(f"date_format {cfg.date_format!r} has no date directives")


def config_from_mapping(raw: Any, source: str = "<mapping>") -> EntitySummarizationConfig:
    """Validated config from a mapping (bare, or wrapped in ``entity_summarization``)."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise EntityConfigError(f"{source}: expected a mapping, got {type(raw).__name__}")
    if set(raw) == {"entity_summarization"}:
        raw = raw["entity_summarization"] or {}
        if not isinstance(raw, dict):
            raise EntityConfigError(f"{source}: entity_summarization must be a mapping")
    unknown = sorted(set(raw) - set(_FIELDS))
    if unknown:
        raise EntityConfigError(f"{source}: unknown entity_summarization key(s) {unknown}; valid: {sorted(_FIELDS)}")
    values = {name: _coerce(name, value, source) for name, value in raw.items()}
    cfg = EntitySummarizationConfig(**values)
    _validate(cfg, source)
    return cfg


def load_yaml_file(path: str | Path) -> EntitySummarizationConfig:
    import yaml

    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise EntityConfigError(f"{p}: cannot read entity summarization config: {e}") from e
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise EntityConfigError(f"{p}: invalid YAML: {e}") from e
    return config_from_mapping(raw, str(p))


def load_entity_config(path: str | Path | None = None, config: Any = None) -> EntitySummarizationConfig:
    """The effective entity summarization config (see module docstring for precedence)."""
    if path is not None:
        return load_yaml_file(path)
    env_path = os.getenv(ENV_VAR)
    if env_path:
        return load_yaml_file(env_path)
    if config is None:
        try:
            from src.core.config import Config

            config = Config.get_instance()
        except Exception as e:  # no config.yml (e.g. a library user): defaults
            logging.getLogger(__name__).debug(f"config.yml unavailable for entity summarization: {e}")
            return EntitySummarizationConfig()
    block = config.get("entity_summarization", None)
    return config_from_mapping(block or {}, "config.yml")
