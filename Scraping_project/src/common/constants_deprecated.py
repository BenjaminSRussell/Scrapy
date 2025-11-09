"""Simplified constants - Delta Lake in root data/ folder only."""

from pathlib import Path
from typing import Final

PROJECT_ROOT: Final[Path] = Path(__file__).parent.parent.parent

DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
DELTA_LAKE: Final[Path] = DATA_DIR / "delta_lake"

CONFIG_DIR: Final[Path] = DATA_DIR / "config"
LOGS_DIR: Final[Path] = DATA_DIR / "logs"
CACHE_DIR: Final[Path] = DATA_DIR / "cache"

DEBERTA_MODEL: Final[str] = "microsoft/deberta-v3-base"

DEFAULT_USER_AGENT: Final[str] = "UConn-Scraper/3.0"
REQUEST_TIMEOUT: Final[int] = 30
MAX_RETRIES: Final[int] = 3
MAX_CONCURRENT_REQUESTS: Final[int] = 16

MAX_PAGE_SIZE: Final[int] = 5 * 1024 * 1024
MIN_CONTENT_LENGTH: Final[int] = 100

SUMMARY_LIMITS: Final[dict[str, int]] = {
    "min_length": 30,
    "max_length": 150,
    "chunk_size": 1024,
    "extractive_max_sentences": 5,
}

LOG_FORMAT: Final[str] = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

def ensure_directories() -> None:
    for directory in [DELTA_LAKE, CONFIG_DIR, LOGS_DIR, CACHE_DIR]:
        directory.mkdir(parents=True, exist_ok=True)

ensure_directories()
