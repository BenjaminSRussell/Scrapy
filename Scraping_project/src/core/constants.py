"""
Global constants for the UConn scraping pipeline.

Consolidated from src/common/constants.py
"""

from pathlib import Path
from typing import Final

# Project paths
PROJECT_ROOT: Final[Path] = Path(__file__).parent.parent.parent
DATA_DIR: Final[Path] = PROJECT_ROOT / "data"
DELTA_LAKE: Final[Path] = DATA_DIR / "delta_lake"
CONFIG_DIR: Final[Path] = DATA_DIR / "config"
LOGS_DIR: Final[Path] = DATA_DIR / "logs"
CACHE_DIR: Final[Path] = DATA_DIR / "cache"

# Pipeline stages
STAGE_1_URL_DISCOVERY = "stage1"
STAGE_2_PAGE_ANALYSIS = "stage2"
STAGE_3_SUMMARIZATION = "stage3"
STAGE_4_LARGE_DOCS = "stage4"

# Delta Lake table names
TABLE_SEED_URLS = "seed_urls"
TABLE_UCONN_URLS = "uconn_urls"
TABLE_STAGE1_DISCOVERY = "stage1_discovery"
TABLE_STAGE1_ERRORS = "stage1_errors"
TABLE_STAGE2_QUEUE = "stage2_queue"
TABLE_STAGE2_PAGE_ANALYSIS = "stage2_page_analysis"
TABLE_STAGE3_SUMMARIES = "stage3_summaries"
TABLE_STAGE4_LARGE_DOC_SUMMARIES = "stage4_large_doc_summaries"

# Document classification thresholds
MIN_QUALITY_WORD_COUNT = 100
MAX_QUALITY_WORD_COUNT = 50000
MASSIVE_DOC_THRESHOLD = 50000

# Text processing
MIN_TEXT_HTML_RATIO = 0.3
MAX_URL_LENGTH = 2048

# Redis key prefixes
REDIS_KEY_SEEN_URLS = "seen:urls"
REDIS_KEY_COUNTER = "counter"

# Default timeouts (seconds)
DEFAULT_TIMEOUT = 30
DEFAULT_POLL_INTERVAL = 5

# File extensions to ignore
IGNORED_EXTENSIONS = [
    '.jpg', '.jpeg', '.png', '.gif', '.svg',
    '.css', '.js', '.woff', '.ttf', '.eot',
    '.mp4', '.mp3', '.avi', '.mov',
    '.zip', '.tar', '.gz', '.pdf'
]

# HTTP status codes
HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR = 500

# Batch sizes
DEFAULT_BATCH_SIZE = 100
MAX_BATCH_SIZE = 1000

# Concurrency limits
MAX_CONCURRENT_REQUESTS = 512
MAX_CONCURRENT_WORKERS = 100

# Summarization limits
SUMMARY_LIMITS: Final[dict[str, int]] = {
    "min_length": 30,
    "max_length": 150,
    "chunk_size": 1024,
    "extractive_max_sentences": 5,
}
