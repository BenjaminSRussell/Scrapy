"""
Centralized Pipeline Configuration Manager

Single source of truth for all pipeline configuration.
Provides type-safe access to configuration with validation.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StageConfig:
    """Configuration for a single pipeline stage"""
    name: str
    output_file: Path
    enabled: bool = True

    @classmethod
    def from_dict(cls, name: str, config: dict[str, Any]) -> 'StageConfig':
        return cls(
            name=name,
            output_file=Path(config.get('output_file', f'data/processed/{name}/output.jsonl')),
            enabled=config.get('enabled', True)
        )


@dataclass(frozen=True)
class PipelineOutputPaths:
    """Centralized output paths for all pipeline stages"""

    # Stage outputs
    stage1_discovery: Path = Path('data/processed/stage01/discovery_output.jsonl')
    stage2_validation: Path = Path('data/processed/stage02/validation_output.jsonl')
    stage3_enrichment: Path = Path('data/processed/stage03/enriched_content.jsonl')

    # Cache and working directories
    cache_dir: Path = Path('data/cache')
    checkpoint_dir: Path = Path('data/checkpoints')
    logs_dir: Path = Path('data/logs')
    temp_dir: Path = Path('data/temp')

    # Deduplication databases
    stage1_dedup: Path = Path('data/cache/stage1_dedup.db')
    stage3_dedup: Path = Path('data/cache/stage3_dedup.db')
    url_cache: Path = Path('data/cache/url_cache.db')

    # Link graph and analytics
    link_graph: Path = Path('data/processed/link_graph.db')
    freshness_db: Path = Path('data/cache/freshness.db')

    def create_directories(self) -> None:
        """Create all required directories"""
        for attr_name in dir(self):
            if attr_name.startswith('_'):
                continue
            attr_value = getattr(self, attr_name)
            if isinstance(attr_value, Path):
                if '.' in attr_value.name:  # File
                    attr_value.parent.mkdir(parents=True, exist_ok=True)
                else:  # Directory
                    attr_value.mkdir(parents=True, exist_ok=True)


class ConfigurationManager:
    """Singleton configuration manager for pipeline"""

    _instance = None
    _paths: PipelineOutputPaths = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._paths = PipelineOutputPaths()
        return cls._instance

    @property
    def paths(self) -> PipelineOutputPaths:
        """Get pipeline output paths"""
        return self._paths

    def ensure_directories(self) -> None:
        """Ensure all directories exist"""
        self._paths.create_directories()

    @staticmethod
    def get_stage_output(stage: int) -> Path:
        """Get output path for a specific stage"""
        config = ConfigurationManager()
        if stage == 1:
            return config.paths.stage1_discovery
        elif stage == 2:
            return config.paths.stage2_validation
        elif stage == 3:
            return config.paths.stage3_enrichment
        else:
            raise ValueError(f"Invalid stage: {stage}")
