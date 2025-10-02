# src/common/config_keys.py

# Top level keys
STAGES = "stages"
DATA = "data"
LOGGING = "logging"
SCRAPY = "scrapy"

# Scrapy settings
SCRAPY_CONCURRENT_REQUESTS = "concurrent_requests"
SCRAPY_CONCURRENT_REQUESTS_PER_DOMAIN = "concurrent_requests_per_domain"
SCRAPY_CONCURRENT_REQUESTS_PER_IP = "concurrent_requests_per_ip"
SCRAPY_DOWNLOAD_DELAY = "download_delay"
SCRAPY_DOWNLOAD_TIMEOUT = "download_timeout"
SCRAPY_DNS_TIMEOUT = "dns_timeout"
SCRAPY_RETRY_ENABLED = "retry_enabled"
SCRAPY_RETRY_TIMES = "retry_times"
SCRAPY_ROBOTSTXT_OBEY = "robotstxt_obey"
SCRAPY_USER_AGENT = "user_agent"
SCRAPY_LOG_LEVEL = "log_level"
SCRAPY_REQUEST_FINGERPRINTER_IMPLEMENTATION = "request_fingerprinter_implementation"

# Stage settings
STAGE_DISCOVERY = "discovery"
STAGE_VALIDATION = "validation"
STAGE_ENRICHMENT = "enrichment"

# Discovery stage settings
DISCOVERY_SPIDER_NAME = "spider_name"
DISCOVERY_ALLOWED_DOMAINS = "allowed_domains"
DISCOVERY_MAX_DEPTH = "max_depth"
DISCOVERY_BATCH_SIZE = "batch_size"
DISCOVERY_OUTPUT_FILE = "output_file"
DISCOVERY_SEED_FILE = "seed_file"
DISCOVERY_USE_PERSISTENT_DEDUP = "use_persistent_dedup"
DISCOVERY_DEDUP_CACHE_PATH = "dedup_cache_path"
DISCOVERY_DYNAMIC_SCRIPT_HINTS = "DISCOVERY_DYNAMIC_SCRIPT_HINTS"
DISCOVERY_HEADLESS_BROWSER = "headless_browser"
DISCOVERY_HEURISTICS = "heuristics"

# Validation stage settings
VALIDATION_MAX_WORKERS = "max_workers"
VALIDATION_TIMEOUT = "timeout"
VALIDATION_OUTPUT_FILE = "output_file"
VALIDATION_RETRY = "retry"
VALIDATION_CIRCUIT_BREAKER = "circuit_breaker"

# Retry configuration keys
RETRY_MAX_ATTEMPTS = "max_attempts"
RETRY_BASE_DELAY = "base_delay"
RETRY_MAX_DELAY = "max_delay"
RETRY_EXPONENTIAL_BASE = "exponential_base"
RETRY_JITTER_FACTOR = "jitter_factor"
RETRY_TRANSIENT_MAX_ATTEMPTS = "transient_max_attempts"
RETRY_RATE_LIMIT_MAX_ATTEMPTS = "rate_limit_max_attempts"
RETRY_RATE_LIMIT_BASE_DELAY = "rate_limit_base_delay"

# Circuit breaker configuration keys
CIRCUIT_BREAKER_FAILURE_THRESHOLD = "failure_threshold"
CIRCUIT_BREAKER_SUCCESS_THRESHOLD = "success_threshold"
CIRCUIT_BREAKER_TIMEOUT = "timeout"

# Enrichment stage settings
ENRICHMENT_SPIDER_NAME = "spider_name"
ENRICHMENT_ALLOWED_DOMAINS = "allowed_domains"
ENRICHMENT_NLP_ENABLED = "nlp_enabled"
ENRICHMENT_MAX_TEXT_LENGTH = "max_text_length"
ENRICHMENT_TOP_KEYWORDS = "top_keywords"
ENRICHMENT_BATCH_SIZE = "batch_size"
ENRICHMENT_OUTPUT_FILE = "output_file"
ENRICHMENT_HEADLESS_BROWSER = "headless_browser"
ENRICHMENT_CONTENT_TYPES = "content_types"
ENRICHMENT_STORAGE = "storage"
STORAGE_BACKEND = "backend"
STORAGE_OPTIONS = "options"
STORAGE_ROTATION = "rotation"
STORAGE_COMPRESSION = "compression"


# NLP settings
NLP = "nlp"
NLP_SPACY_MODEL = "spacy_model"
NLP_USE_TRANSFORMERS = "use_transformers"
NLP_TRANSFORMER_NER_MODEL = "transformer_ner_model"
NLP_SUMMARIZER_MODEL = "summarizer_model"
NLP_MAX_TEXT_LENGTH = "max_text_length"
NLP_TOP_KEYWORDS = "top_keywords"
NLP_SUMMARY_MAX_LENGTH = "summary_max_length"
NLP_SUMMARY_MIN_LENGTH = "summary_min_length"
NLP_DEVICE = "device"

# Logging settings
LOGGING_LEVEL = "level"
LOGGING_FORMAT = "format"
LOGGING_FILE = "file"

# Data paths
RAW_DIR = "raw_dir"
PROCESSED_DIR = "processed_dir"
CATALOG_DIR = "catalog_dir"
CACHE_DIR = "cache_dir"
EXPORTS_DIR = "exports_dir"
LOGS_DIR = "logs_dir"
TEMP_DIR = "temp_dir"