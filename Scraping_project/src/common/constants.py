"""
Global constants and configuration for the UConn scraping pipeline.
Centralized location for all shared constants, paths, and configuration.
"""

from pathlib import Path
from typing import Final

# Project root directory
PROJECT_ROOT: Final[Path] = Path(__file__).parent.parent.parent

# Data directories - SINGLE SOURCE OF TRUTH
DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
OUTPUT_DIR: Final[Path] = DATA_DIR / "output"  # Single unified output location
LOGS_DIR: Final[Path] = DATA_DIR / "logs"
CHECKPOINTS_DIR: Final[Path] = DATA_DIR / "checkpoints"
CONFIG_DIR: Final[Path] = DATA_DIR / "config"
CACHE_DIR: Final[Path] = DATA_DIR / "cache"

# Legacy directories (to be removed/migrated)
LEGACY_SCRAPY_DATA: Final[Path] = PROJECT_ROOT / ".scrapy" / "data"

# Output file patterns
DISCOVERED_URLS_PATTERN: Final[str] = "discovered_urls_*.jsonl"
VALIDATED_URLS_PATTERN: Final[str] = "validated_urls_*.jsonl"
ENRICHED_PAGES_PATTERN: Final[str] = "enriched_pages_*.jsonl"

# Stage identifiers
STAGE_DISCOVERY: Final[str] = "stage1_discovery"
STAGE_VALIDATION: Final[str] = "stage2_validation"
STAGE_ENRICHMENT: Final[str] = "stage3_enrichment"

# NLP Configuration
DEFAULT_SPACY_MODEL: Final[str] = "en_core_web_sm"
LARGE_SPACY_MODEL: Final[str] = "en_core_web_lg"
TRANSFORMER_MODEL: Final[str] = "dslim/bert-base-NER"

# Taxonomy and glossary paths
TAXONOMY_PATH: Final[Path] = CONFIG_DIR / "taxonomy.json"
GLOSSARY_PATH: Final[Path] = CONFIG_DIR / "uconn_glossary.json"

# HTTP Configuration
DEFAULT_USER_AGENT: Final[str] = "UConnBot/2.0 (University Research; +https://uconn.edu/bot)"
REQUEST_TIMEOUT: Final[int] = 30
MAX_RETRIES: Final[int] = 3

# Concurrency limits
MAX_CONCURRENT_REQUESTS: Final[int] = 16
MAX_CONCURRENT_REQUESTS_PER_DOMAIN: Final[int] = 8

# Content limits
MAX_PAGE_SIZE: Final[int] = 5 * 1024 * 1024  # 5MB
MIN_CONTENT_LENGTH: Final[int] = 100
MAX_CONTENT_LENGTH: Final[int] = 1_000_000

# Entity filtering
MAX_ENTITY_WORDS: Final[int] = 6
MIN_ENTITY_LENGTH: Final[int] = 2

# Database configuration
DEFAULT_DB_TYPE: Final[str] = "sqlite"
SQLITE_DB_PATH: Final[Path] = DATA_DIR / "warehouse" / "uconn_warehouse.db"

# Logging configuration
LOG_FORMAT: Final[str] = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"
LOG_FILE_MAX_BYTES: Final[int] = 10 * 1024 * 1024  # 10MB
LOG_FILE_BACKUP_COUNT: Final[int] = 5

# Checkpoint configuration
CHECKPOINT_BATCH_SIZE: Final[int] = 100
CHECKPOINT_FLUSH_INTERVAL: Final[int] = 300  # 5 minutes

# Metrics and monitoring
METRICS_SERVER_HOST: Final[str] = "localhost"
METRICS_SERVER_PORT: Final[int] = 8765
METRICS_ENABLED: Final[bool] = True

# Visualizer configuration
VISUALIZER_HOST: Final[str] = "localhost"
VISUALIZER_PORT: Final[int] = 8080
VISUALIZER_WEBSOCKET_PATH: Final[str] = "/ws"
VISUALIZER_EVENT_PATH: Final[str] = "/event"


def ensure_directories() -> None:
    """Create all necessary directories if they don't exist."""
    directories = [
        OUTPUT_DIR,
        LOGS_DIR,
        CHECKPOINTS_DIR,
        CONFIG_DIR,
        CACHE_DIR,
        OUTPUT_DIR / "stage1",
        OUTPUT_DIR / "stage2",
        OUTPUT_DIR / "stage3",
        DATA_DIR / "warehouse",
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


def get_output_path(stage: str, filename: str) -> Path:
    """
    Get the standardized output path for a given stage and filename.

    Args:
        stage: Stage identifier (use STAGE_* constants)
        filename: Output filename

    Returns:
        Path: Full path to output file
    """
    stage_dir = OUTPUT_DIR / stage.replace("stage", "stage")
    stage_dir.mkdir(parents=True, exist_ok=True)
    return stage_dir / filename


def get_log_path(component: str) -> Path:
    """
    Get the standardized log path for a component.

    Args:
        component: Component name (e.g., "pipeline", "enrichment")

    Returns:
        Path: Full path to log file
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / f"{component}.log"


# Initialize directories on import
ensure_directories()
