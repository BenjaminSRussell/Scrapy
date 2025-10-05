"""
Global constants and configuration for the UConn scraping pipeline.
Centralized location for all shared constants, paths, and configuration.
"""

from pathlib import Path
from typing import Final

# Project root directory
PROJECT_ROOT: Final[Path] = Path(__file__).parent.parent.parent

# Data architecture - SINGLE SOURCE OF TRUTH (Delta Lake primary)
DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
DATALAKE_DIR: Final[Path] = DATA_DIR / "datalake"
CONFIG_DIR: Final[Path] = DATA_DIR / "config"
LOGS_DIR: Final[Path] = DATA_DIR / "logs"
CACHE_DIR: Final[Path] = DATA_DIR / "cache"
CHECKPOINTS_DIR: Final[Path] = DATA_DIR / "checkpoints"

# Delta Lake tables (primary storage)
DELTA_RAW_URLS: Final[Path] = DATALAKE_DIR / "raw_urls"
DELTA_VALIDATED_URLS: Final[Path] = DATALAKE_DIR / "validated_urls"
DELTA_ENRICHED_CONTENT: Final[Path] = DATALAKE_DIR / "enriched_content"
DELTA_LINK_GRAPH: Final[Path] = DATALAKE_DIR / "link_graph"
DELTA_PERFORMANCE_METRICS: Final[Path] = DATALAKE_DIR / "performance_metrics"

# Legacy paths (for backward compatibility - will be phased out)
LEGACY_PROCESSED_DIR: Final[Path] = DATA_DIR / "processed"
LINK_GRAPH_DB: Final[Path] = LEGACY_PROCESSED_DIR / "link_graph.db"
FRESHNESS_DB: Final[Path] = CACHE_DIR / "freshness.db"
WAREHOUSE_DB: Final[Path] = DATA_DIR / "warehouse" / "uconn_warehouse.db"
WAREHOUSE_DIR: Final[Path] = WAREHOUSE_DB.parent

# Stage identifiers
STAGE_DISCOVERY: Final[str] = "stage1_discovery"
STAGE_VALIDATION: Final[str] = "stage2_validation"
STAGE_ENRICHMENT: Final[str] = "stage3_enrichment"

# NLP Configuration
DEFAULT_SPACY_MODEL: Final[str] = "en_core_web_sm"  # Used for keywords and linguistic analysis
LARGE_SPACY_MODEL: Final[str] = "en_core_web_lg"
TRANSFORMER_MODEL: Final[str] = "microsoft/deberta-v3-base"  # DeBERTa model for NER
SUMMARIZATION_MODEL: Final[str] = "facebook/bart-large-cnn"  # For long-form summarization
WHISPER_MODEL: Final[str] = "base"  # Whisper model for audio transcription (tiny/base/small/medium/large)

# Content Processing
SUPPORTED_IMAGE_TYPES: Final[tuple] = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp')
SUPPORTED_AUDIO_TYPES: Final[tuple] = ('.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac')
SUPPORTED_VIDEO_TYPES: Final[tuple] = ('.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv')
SUPPORTED_DOC_TYPES: Final[tuple] = ('.pdf', '.doc', '.docx', '.txt', '.rtf')

# OCR Configuration
OCR_ENGINE: Final[str] = "easyocr"  # Options: 'easyocr', 'tesseract', 'paddleocr'
OCR_LANGUAGES: Final[list] = ['en']  # Languages for OCR
MAX_IMAGE_SIZE_MB: Final[int] = 10  # Maximum image size for OCR

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
SQLITE_DB_PATH: Final[Path] = WAREHOUSE_DB

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
        WAREHOUSE_DIR,
        CONFIG_DIR,
        LOGS_DIR,
        CACHE_DIR,
    ]

    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


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
