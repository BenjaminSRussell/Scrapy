"""
Pydantic-based configuration schema with strict validation.
Catches type errors, range violations, and typos (unknown keys).
"""

from typing import List, Dict, Any, Optional, Literal
from pathlib import Path
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
import logging

logger = logging.getLogger(__name__)


class ScrapyConfig(BaseModel):
    """Scrapy framework configuration"""
    model_config = ConfigDict(extra='forbid')  # Reject unknown keys

    concurrent_requests: int = Field(
        default=32,
        ge=1,
        le=1000,
        description="Maximum concurrent requests"
    )
    concurrent_requests_per_domain: int = Field(
        default=16,
        ge=1,
        le=100,
        description="Max concurrent requests per domain"
    )
    concurrent_requests_per_ip: int = Field(
        default=16,
        ge=1,
        le=100,
        description="Max concurrent requests per IP"
    )
    download_delay: float = Field(
        default=0.1,
        ge=0.0,
        le=60.0,
        description="Download delay in seconds"
    )
    download_timeout: int = Field(
        default=10,
        ge=1,
        le=300,
        description="Download timeout in seconds"
    )
    dns_timeout: int = Field(
        default=5,
        ge=1,
        le=60,
        description="DNS timeout in seconds"
    )
    retry_enabled: bool = Field(
        default=True,
        description="Enable request retries"
    )
    retry_times: int = Field(
        default=2,
        ge=0,
        le=10,
        description="Number of retry attempts"
    )
    robotstxt_obey: bool = Field(
        default=False,
        description="Obey robots.txt rules"
    )
    user_agent: str = Field(
        default="UConn-Discovery-Crawler/1.0",
        min_length=1,
        description="User agent string"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Scrapy log level"
    )
    request_fingerprinter_implementation: str = Field(
        default="2.7",
        description="Request fingerprinter version"
    )

    @field_validator('concurrent_requests_per_domain', 'concurrent_requests_per_ip')
    @classmethod
    def validate_concurrency_limits(cls, v, info):
        """Ensure per-domain/IP limits don't exceed total concurrent requests"""
        # Note: We can't access concurrent_requests here in field_validator
        # This will be checked in model_validator
        return v

    @model_validator(mode='after')
    def validate_concurrency_hierarchy(self):
        """Ensure per-domain and per-IP limits are less than total"""
        if self.concurrent_requests_per_domain > self.concurrent_requests:
            raise ValueError(
                f"concurrent_requests_per_domain ({self.concurrent_requests_per_domain}) "
                f"cannot exceed concurrent_requests ({self.concurrent_requests})"
            )
        if self.concurrent_requests_per_ip > self.concurrent_requests:
            raise ValueError(
                f"concurrent_requests_per_ip ({self.concurrent_requests_per_ip}) "
                f"cannot exceed concurrent_requests ({self.concurrent_requests})"
            )
        return self


class HeadlessBrowserViewport(BaseModel):
    """Viewport configuration for headless browser"""
    model_config = ConfigDict(extra='forbid')

    width: int = Field(
        default=1920,
        ge=320,
        le=7680,
        description="Viewport width in pixels"
    )
    height: int = Field(
        default=1080,
        ge=240,
        le=4320,
        description="Viewport height in pixels"
    )


class HeadlessBrowserConfig(BaseModel):
    """Headless browser configuration for JavaScript-rendered content"""
    model_config = ConfigDict(extra='forbid')

    enabled: bool = Field(
        default=False,
        description="Enable headless browser"
    )
    engine: Literal["playwright", "selenium"] = Field(
        default="playwright",
        description="Browser automation engine"
    )
    browser_type: Literal["chromium", "firefox", "webkit", "chrome"] = Field(
        default="chromium",
        description="Browser type"
    )
    headless: bool = Field(
        default=True,
        description="Run browser in headless mode"
    )
    timeout: int = Field(
        default=30000,
        ge=1000,
        le=300000,
        description="Page load timeout in milliseconds"
    )
    wait_for_selector: Optional[str] = Field(
        default=None,
        description="CSS selector to wait for (optional)"
    )
    screenshot_on_error: bool = Field(
        default=False,
        description="Capture screenshot on errors"
    )
    user_agent: Optional[str] = Field(
        default="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        description="Browser user agent"
    )
    viewport: HeadlessBrowserViewport = Field(
        default_factory=HeadlessBrowserViewport,
        description="Browser viewport settings"
    )
    concurrent_limit: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Maximum concurrent browser instances"
    )

    # Enhanced discovery features
    enable_network_intercept: bool = Field(
        default=True,
        description="Enable network request/response interception"
    )
    enable_auto_click: bool = Field(
        default=True,
        description="Auto-click 'Load More' buttons"
    )
    enable_infinite_scroll: bool = Field(
        default=True,
        description="Handle infinite scroll pages"
    )
    max_scroll_attempts: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum scroll attempts for infinite scroll"
    )
    scroll_pause_ms: int = Field(
        default=500,
        ge=100,
        le=5000,
        description="Pause duration between scroll attempts (milliseconds)"
    )

    @model_validator(mode='after')
    def validate_engine_browser_compatibility(self):
        """Ensure browser type is compatible with engine"""
        if self.engine == "selenium" and self.browser_type == "webkit":
            raise ValueError(
                "Selenium does not support WebKit browser. "
                "Use 'chromium', 'firefox', or 'chrome', or switch to 'playwright' engine."
            )
        return self


class PDFConfig(BaseModel):
    """PDF processing configuration"""
    model_config = ConfigDict(extra='forbid')

    extract_text: bool = Field(
        default=True,
        description="Extract text from PDFs"
    )
    extract_metadata: bool = Field(
        default=True,
        description="Extract PDF metadata"
    )
    max_pages: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Maximum pages to process per PDF"
    )


class MediaConfig(BaseModel):
    """Media (image/video/audio) processing configuration"""
    model_config = ConfigDict(extra='forbid')

    extract_metadata: bool = Field(
        default=True,
        description="Extract media metadata"
    )
    download_thumbnails: bool = Field(
        default=False,
        description="Generate and save thumbnails"
    )
    thumbnail_dir: str = Field(
        default="data/processed/media/thumbnails",
        min_length=1,
        description="Thumbnail storage directory"
    )


class ContentTypesConfig(BaseModel):
    """Content type handling configuration"""
    model_config = ConfigDict(extra='forbid')

    enabled_types: List[str] = Field(
        default=[
            "text/html",
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/gif",
            "video/mp4",
            "video/webm",
            "audio/mpeg",
            "audio/wav"
        ],
        min_length=1,
        description="List of enabled MIME types"
    )
    pdf: PDFConfig = Field(
        default_factory=PDFConfig,
        description="PDF-specific configuration"
    )
    media: MediaConfig = Field(
        default_factory=MediaConfig,
        description="Media-specific configuration"
    )

    @field_validator('enabled_types')
    @classmethod
    def validate_mime_types(cls, v):
        """Validate MIME type format"""
        valid_mime_pattern = r'^[a-z]+/[a-z0-9\-\+\.]+$'
        import re
        for mime_type in v:
            if not re.match(valid_mime_pattern, mime_type, re.IGNORECASE):
                raise ValueError(f"Invalid MIME type format: {mime_type}")
        return v


class HeuristicsConfig(BaseModel):
    """Discovery heuristics configuration"""
    model_config = ConfigDict(extra='forbid')

    # Feature flags to enable/disable specific heuristics
    enable_json_discovery: bool = Field(
        default=True,
        description="Enable JSON blob parsing for URL discovery"
    )
    enable_ajax_regex: bool = Field(
        default=True,
        description="Enable AJAX endpoint regex pattern matching"
    )
    enable_pagination_guess: bool = Field(
        default=True,
        description="Enable automatic pagination URL generation"
    )
    enable_data_attributes: bool = Field(
        default=True,
        description="Enable parsing of HTML data attributes for URLs"
    )
    enable_form_actions: bool = Field(
        default=True,
        description="Enable extraction of form action URLs"
    )
    enable_meta_refresh: bool = Field(
        default=True,
        description="Enable extraction of meta refresh redirect URLs"
    )

    dynamic_script_hints: List[str] = Field(
        default=[
            "fetch(", "xmlhttprequest", "axios", "$.get", "$.post",
            ".ajax", "loadmore", "nexturl", "next_url", "load_more",
            "apiurl", "api_url", "dispatch("
        ],
        description="JavaScript patterns indicating dynamic content"
    )
    json_url_key_hints: List[str] = Field(
        default=["url", "href", "link", "endpoint", "action", "download"],
        description="JSON keys likely to contain URLs"
    )
    data_attribute_candidates: List[str] = Field(
        default=[
            "data-url", "data-src", "data-endpoint", "data-load",
            "data-load-url", "data-href", "data-link", "data-api",
            "data-request-url", "data-action", "data-next", "data-feed"
        ],
        description="HTML data attributes that may contain URLs"
    )

    # Throttling and quality control
    min_confidence_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score for discovered URLs (0.0-1.0)"
    )
    max_low_quality_per_page: int = Field(
        default=50,
        ge=0,
        le=1000,
        description="Maximum low-quality URLs to accept per page"
    )
    pagination_max_pages: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum pagination pages to generate per endpoint"
    )


class DiscoveryStageConfig(BaseModel):
    """Stage 1: Discovery configuration"""
    model_config = ConfigDict(extra='forbid')

    spider_name: str = Field(
        default="discovery",
        min_length=1,
        description="Spider name"
    )
    allowed_domains: List[str] = Field(
        default=["uconn.edu"],
        min_length=1,
        description="List of allowed domains to crawl"
    )
    max_depth: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Maximum crawl depth"
    )
    batch_size: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="Batch size for processing"
    )
    output_file: str = Field(
        default="data/processed/stage01/discovery_output.jsonl",
        min_length=1,
        description="Output file path"
    )
    seed_file: str = Field(
        default="data/raw/uconn_urls.csv",
        min_length=1,
        description="Seed URLs file path"
    )
    use_persistent_dedup: bool = Field(
        default=True,
        description="Use persistent URL deduplication"
    )
    dedup_cache_path: str = Field(
        default="data/cache/url_cache.db",
        min_length=1,
        description="Deduplication cache database path"
    )
    headless_browser: HeadlessBrowserConfig = Field(
        default_factory=HeadlessBrowserConfig,
        description="Headless browser configuration"
    )
    heuristics: HeuristicsConfig = Field(
        default_factory=HeuristicsConfig,
        description="Discovery heuristics"
    )

    @field_validator('allowed_domains')
    @classmethod
    def validate_domains(cls, v):
        """Validate domain format"""
        import re
        domain_pattern = r'^([a-z0-9]+(-[a-z0-9]+)*\.)+[a-z]{2,}$'
        for domain in v:
            if not re.match(domain_pattern, domain, re.IGNORECASE):
                raise ValueError(f"Invalid domain format: {domain}")
        return v


class RetryConfig(BaseModel):
    """Retry strategy configuration"""
    model_config = ConfigDict(extra='forbid')

    max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum retry attempts for most errors"
    )
    base_delay: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Base delay in seconds for exponential backoff"
    )
    max_delay: float = Field(
        default=60.0,
        ge=1.0,
        le=600.0,
        description="Maximum delay in seconds"
    )
    exponential_base: float = Field(
        default=2.0,
        ge=1.5,
        le=10.0,
        description="Exponential backoff base"
    )
    jitter_factor: float = Field(
        default=0.1,
        ge=0.0,
        le=0.5,
        description="Jitter factor (±percentage) to prevent thundering herd"
    )
    transient_max_attempts: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum attempts for transient errors (network hiccups)"
    )
    rate_limit_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Maximum attempts for rate limit errors"
    )
    rate_limit_base_delay: float = Field(
        default=5.0,
        ge=1.0,
        le=60.0,
        description="Base delay for rate limit errors"
    )

    @model_validator(mode='after')
    def validate_delay_hierarchy(self):
        """Ensure max_delay is greater than base_delay"""
        if self.max_delay < self.base_delay:
            raise ValueError(
                f"max_delay ({self.max_delay}) must be >= base_delay ({self.base_delay})"
            )
        return self


class CircuitBreakerConfig(BaseModel):
    """Circuit breaker configuration"""
    model_config = ConfigDict(extra='forbid')

    failure_threshold: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Number of failures before opening circuit"
    )
    success_threshold: int = Field(
        default=2,
        ge=1,
        le=10,
        description="Number of successes to close circuit from half-open"
    )
    timeout: float = Field(
        default=60.0,
        ge=1.0,
        le=3600.0,
        description="Time in seconds before attempting to close circuit"
    )

    @model_validator(mode='after')
    def validate_thresholds(self):
        """Ensure failure_threshold is greater than success_threshold"""
        if self.failure_threshold < self.success_threshold:
            raise ValueError(
                f"failure_threshold ({self.failure_threshold}) should be >= "
                f"success_threshold ({self.success_threshold})"
            )
        return self


class ValidationStageConfig(BaseModel):
    """Stage 2: Validation configuration"""
    model_config = ConfigDict(extra='forbid')

    max_workers: int = Field(
        default=16,
        ge=1,
        le=100,
        description="Maximum concurrent workers"
    )
    timeout: int = Field(
        default=15,
        ge=1,
        le=300,
        description="Request timeout in seconds"
    )
    output_file: str = Field(
        default="data/processed/stage02/validated_urls.jsonl",
        min_length=1,
        description="Output file path"
    )
    retry: RetryConfig = Field(
        default_factory=RetryConfig,
        description="Retry strategy configuration"
    )
    circuit_breaker: CircuitBreakerConfig = Field(
        default_factory=CircuitBreakerConfig,
        description="Circuit breaker configuration"
    )


class StorageRotationConfig(BaseModel):
    """Rotation configuration for enrichment storage."""
    model_config = ConfigDict(extra='forbid')

    max_bytes: Optional[int] = Field(default=None, ge=1, description='Rotate after writing this many bytes')
    max_items: Optional[int] = Field(default=None, ge=1, description='Rotate after writing this many items')
    max_seconds: Optional[int] = Field(default=None, ge=1, description='Rotate after this many seconds')
    enabled: Optional[bool] = Field(default=None, description='Explicitly enable or disable rotation')


class StorageCompressionConfig(BaseModel):
    """Compression configuration for enrichment storage."""
    model_config = ConfigDict(extra='forbid')

    codec: Literal['none', 'gzip', 'snappy', 'brotli', 'zstd'] = Field(
        default='none',
        description='Compression codec to apply'
    )
    level: Optional[int] = Field(
        default=None,
        ge=1,
        le=9,
        description='Compression level where applicable'
    )
    use_extension: bool = Field(
        default=True,
        description='Append codec-specific extension to generated files'
    )

    @model_validator(mode='after')
    def validate_level_usage(self):
        """Ensure compression level is only set when meaningful."""
        if self.codec == 'none' and self.level is not None:
            raise ValueError('Compression level should not be set when codec is "none"')
        return self


class EnrichmentStorageConfig(BaseModel):
    """Storage configuration for Stage 3 outputs."""
    model_config = ConfigDict(extra='forbid')

    backend: Literal['jsonl', 'sqlite', 'parquet', 's3'] = Field(
        default='jsonl',
        description='Storage backend implementation to use'
    )
    options: Dict[str, Any] = Field(
        default_factory=dict,
        description='Backend-specific options dictionary'
    )
    rotation: Optional[StorageRotationConfig] = Field(
        default=None,
        description='Rotation policy configuration'
    )
    compression: Optional[StorageCompressionConfig] = Field(
        default=None,
        description='Compression configuration'
    )

    @model_validator(mode='after')
    def validate_backend_options(self):
        """Validate backend-specific required options."""
        if self.backend == 's3' and not self.options.get('bucket'):
            raise ValueError("S3 storage backend requires a 'bucket' option")
        return self


class EnrichmentStageConfig(BaseModel):
    """Stage 3: Enrichment configuration"""
    model_config = ConfigDict(extra='forbid')

    spider_name: str = Field(
        default="enrichment",
        min_length=1,
        description="Spider name"
    )
    allowed_domains: List[str] = Field(
        default=["uconn.edu"],
        min_length=1,
        description="List of allowed domains"
    )
    nlp_enabled: bool = Field(
        default=True,
        description="Enable NLP processing"
    )
    max_text_length: int = Field(
        default=20000,
        ge=100,
        le=1000000,
        description="Maximum text length to process"
    )
    top_keywords: int = Field(
        default=15,
        ge=1,
        le=100,
        description="Number of top keywords to extract"
    )
    batch_size: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Batch size for processing"
    )
    output_file: str = Field(
        default="data/processed/stage03/enrichment_output.jsonl",
        min_length=1,
        description="Output file path"
    )
    headless_browser: HeadlessBrowserConfig = Field(
        default_factory=HeadlessBrowserConfig,
        description="Headless browser configuration"
    )
    content_types: ContentTypesConfig = Field(
        default_factory=ContentTypesConfig,
        description="Content type handling configuration"
    )
    storage: EnrichmentStorageConfig = Field(
        default_factory=EnrichmentStorageConfig,
        description="Storage backend configuration"
    )


    @field_validator('allowed_domains')
    @classmethod
    def validate_domains(cls, v):
        """Validate domain format"""
        import re
        domain_pattern = r'^([a-z0-9]+(-[a-z0-9]+)*\.)+[a-z]{2,}$'
        for domain in v:
            if not re.match(domain_pattern, domain, re.IGNORECASE):
                raise ValueError(f"Invalid domain format: {domain}")
        return v


class StagesConfig(BaseModel):
    """All pipeline stages configuration"""
    model_config = ConfigDict(extra='forbid')

    discovery: DiscoveryStageConfig = Field(
        default_factory=DiscoveryStageConfig,
        description="Discovery stage configuration"
    )
    validation: ValidationStageConfig = Field(
        default_factory=ValidationStageConfig,
        description="Validation stage configuration"
    )
    enrichment: EnrichmentStageConfig = Field(
        default_factory=EnrichmentStageConfig,
        description="Enrichment stage configuration"
    )


class DataPathsConfig(BaseModel):
    """Data directory paths configuration"""
    model_config = ConfigDict(extra='forbid')

    raw_dir: str = Field(
        default="data/raw",
        min_length=1,
        description="Raw data directory"
    )
    processed_dir: str = Field(
        default="data/processed",
        min_length=1,
        description="Processed data directory"
    )
    catalog_dir: str = Field(
        default="data/catalog",
        min_length=1,
        description="Catalog directory"
    )
    cache_dir: str = Field(
        default="data/cache",
        min_length=1,
        description="Cache directory"
    )
    exports_dir: str = Field(
        default="data/exports",
        min_length=1,
        description="Exports directory"
    )
    logs_dir: str = Field(
        default="data/logs",
        min_length=1,
        description="Logs directory"
    )
    temp_dir: str = Field(
        default="data/temp",
        min_length=1,
        description="Temporary files directory"
    )


class QueueConfig(BaseModel):
    """Queue backpressure configuration"""
    model_config = ConfigDict(extra='forbid')

    max_queue_size: int = Field(
        default=10000,
        ge=100,
        le=1000000,
        description="Maximum queue size"
    )
    batch_size: int = Field(
        default=1000,
        ge=1,
        le=10000,
        description="Batch size"
    )
    backpressure_warning_threshold: float = Field(
        default=0.8,
        ge=0.0,
        le=1.0,
        description="Warning threshold (0.0-1.0)"
    )
    backpressure_critical_threshold: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Critical threshold (0.0-1.0)"
    )

    @model_validator(mode='after')
    def validate_thresholds(self):
        """Ensure warning threshold is less than critical"""
        if self.backpressure_warning_threshold >= self.backpressure_critical_threshold:
            raise ValueError(
                f"backpressure_warning_threshold ({self.backpressure_warning_threshold}) "
                f"must be less than backpressure_critical_threshold "
                f"({self.backpressure_critical_threshold})"
            )
        return self

    @model_validator(mode='after')
    def validate_batch_size(self):
        """Ensure batch size is reasonable relative to queue size"""
        if self.batch_size > self.max_queue_size:
            raise ValueError(
                f"batch_size ({self.batch_size}) cannot exceed "
                f"max_queue_size ({self.max_queue_size})"
            )
        return self


class LoggingConfig(BaseModel):
    """Logging configuration"""
    model_config = ConfigDict(extra='forbid')

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Log level"
    )
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        min_length=1,
        description="Log format string"
    )
    file: str = Field(
        default="data/logs/pipeline.log",
        min_length=1,
        description="Log file path"
    )
    max_bytes: int = Field(
        default=10485760,  # 10MB
        ge=1024,
        le=1073741824,  # 1GB
        description="Maximum log file size in bytes"
    )
    backup_count: int = Field(
        default=3,
        ge=0,
        le=100,
        description="Number of backup log files"
    )
    structured: bool = Field(
        default=False,
        description="Use structured JSON logging"
    )


class NLPConfig(BaseModel):
    """NLP processing configuration"""
    model_config = ConfigDict(extra='forbid')

    # spaCy configuration
    spacy_model: str = Field(
        default="en_core_web_sm",
        min_length=1,
        description="spaCy model name for basic NLP"
    )

    # Transformer models configuration
    use_transformers: bool = Field(
        default=False,
        description="Enable transformer-based models for advanced NLP"
    )
    transformer_ner_model: Optional[str] = Field(
        default="dslim/bert-base-NER",
        description="Transformer model for Named Entity Recognition"
    )
    summarizer_model: Optional[str] = Field(
        default="sshleifer/distilbart-cnn-12-6",
        description="Transformer model for text summarization"
    )

    # Processing settings
    max_text_length: int = Field(
        default=20000,
        ge=100,
        le=1000000,
        description="Maximum text length to process"
    )
    top_keywords: int = Field(
        default=15,
        ge=1,
        le=100,
        description="Top keywords to extract"
    )

    # Summarization settings
    summary_max_length: int = Field(
        default=150,
        ge=50,
        le=500,
        description="Maximum length of generated summary"
    )
    summary_min_length: int = Field(
        default=30,
        ge=10,
        le=200,
        description="Minimum length of generated summary"
    )

    # Device configuration
    device: Optional[Literal["cpu", "cuda", "mps", "auto"]] = Field(
        default="auto",
        description="Device for transformer models (auto, cpu, cuda, mps)"
    )

    # Legacy compatibility
    model: Optional[str] = Field(
        default=None,
        description="Deprecated: use spacy_model instead"
    )


class ContentConfig(BaseModel):
    """Content filtering configuration"""
    model_config = ConfigDict(extra='forbid')

    predefined_tags: List[str] = Field(
        default=[
            "admissions", "about", "research", "students", "faculty",
            "staff", "alumni", "athletics", "covid", "graduate",
            "undergraduate", "catalog", "courses", "registrar",
            "financial-aid", "scholarships", "library", "majors",
            "minors", "housing", "dining", "parking", "sustainability"
        ],
        description="Predefined content tags"
    )


class EmailChannelConfig(BaseModel):
    """Email alert channel configuration"""
    model_config = ConfigDict(extra='forbid')

    type: Literal["email"] = "email"
    smtp_host: str = Field(min_length=1)
    smtp_port: int = Field(ge=1, le=65535)
    smtp_user: str = Field(min_length=1)
    smtp_password: str = Field(min_length=1)
    from_addr: str = Field(min_length=1)
    to_addrs: List[str] = Field(min_length=1)
    use_tls: bool = True


class WebhookChannelConfig(BaseModel):
    """Webhook alert channel configuration"""
    model_config = ConfigDict(extra='forbid')

    type: Literal["webhook"] = "webhook"
    url: str = Field(min_length=1)
    format: Literal["json", "slack", "discord"] = "json"


class FileChannelConfig(BaseModel):
    """File alert channel configuration"""
    model_config = ConfigDict(extra='forbid')

    type: Literal["file"] = "file"
    path: str = Field(min_length=1)
    format: Literal["text", "json"] = "text"


class AlertsConfig(BaseModel):
    """Alerting configuration"""
    model_config = ConfigDict(extra='forbid')

    enabled: bool = Field(
        default=False,
        description="Enable alerting"
    )
    severity_threshold: Literal["info", "warning", "error", "critical"] = Field(
        default="error",
        description="Minimum severity to alert"
    )
    alert_file: str = Field(
        default="data/logs/alerts.jsonl",
        min_length=1,
        description="Alert log file path"
    )
    channels: List[Dict[str, Any]] = Field(
        default=[],
        description="Alert channels configuration"
    )


class PipelineConfig(BaseModel):
    """Complete pipeline configuration schema"""
    model_config = ConfigDict(extra='forbid')  # Reject any unknown keys

    environment: str = Field(
        default="development",
        description="Environment name"
    )
    scrapy: ScrapyConfig = Field(
        default_factory=ScrapyConfig,
        description="Scrapy configuration"
    )
    stages: StagesConfig = Field(
        default_factory=StagesConfig,
        description="Pipeline stages configuration"
    )
    data: DataPathsConfig = Field(
        default_factory=DataPathsConfig,
        description="Data paths configuration"
    )
    queue: QueueConfig = Field(
        default_factory=QueueConfig,
        description="Queue configuration"
    )
    logging: LoggingConfig = Field(
        default_factory=LoggingConfig,
        description="Logging configuration"
    )
    nlp: NLPConfig = Field(
        default_factory=NLPConfig,
        description="NLP configuration"
    )
    content: ContentConfig = Field(
        default_factory=ContentConfig,
        description="Content configuration"
    )
    alerts: AlertsConfig = Field(
        default_factory=AlertsConfig,
        description="Alerts configuration"
    )

    @model_validator(mode='after')
    def validate_seed_file_exists(self):
        """Validate that seed file exists if specified"""
        from pathlib import Path

        seed_file = self.stages.discovery.seed_file
        if seed_file:
            seed_path = Path(seed_file)
            if not seed_path.exists():
                logger.warning(
                    f"Seed file '{seed_file}' does not exist. "
                    f"Discovery stage may fail if this file is required."
                )
        return self

    @model_validator(mode='after')
    def validate_nlp_model_availability(self):
        """Warn if NLP is enabled but model might not be installed"""
        if self.stages.enrichment.nlp_enabled:
            # Check if spaCy model is likely installed
            try:
                import spacy
                model_name = self.nlp.spacy_model or self.nlp.model
                if model_name:
                    try:
                        spacy.load(model_name)
                    except OSError:
                        logger.warning(
                            f"spaCy model '{model_name}' not found. "
                            f"Run: python -m spacy download {model_name}"
                        )
            except ImportError:
                logger.warning("spaCy not installed but NLP is enabled")
        return self

    @model_validator(mode='after')
    def validate_headless_browser_dependencies(self):
        """Warn if headless browser is enabled but dependencies might not be installed"""
        stages_to_check = [
            ('discovery', self.stages.discovery.headless_browser),
            ('enrichment', self.stages.enrichment.headless_browser)
        ]

        for stage_name, browser_config in stages_to_check:
            if browser_config.enabled:
                engine = browser_config.engine
                if engine == 'playwright':
                    try:
                        import playwright
                    except ImportError:
                        logger.warning(
                            f"Playwright not installed but headless browser is enabled in {stage_name} stage. "
                            f"Run: pip install playwright && playwright install"
                        )
                elif engine == 'selenium':
                    try:
                        import selenium
                    except ImportError:
                        logger.warning(
                            f"Selenium not installed but headless browser is enabled in {stage_name} stage. "
                            f"Run: pip install selenium"
                        )
        return self

    @model_validator(mode='after')
    def validate_output_file_directories(self):
        """Validate that parent directories for output files exist or can be created"""
        from pathlib import Path

        output_files = [
            ('discovery', self.stages.discovery.output_file),
            ('validation', self.stages.validation.output_file),
        ]

        enrichment_paths = [self.stages.enrichment.output_file]
        storage = getattr(self.stages.enrichment, 'storage', None)
        if storage and storage.backend != 's3':
            storage_path = storage.options.get('path')
            if storage_path:
                enrichment_paths.append(storage_path)

        for path_value in enrichment_paths:
            output_files.append(('enrichment', path_value))

        for stage_name, output_file in output_files:
            if output_file:
                output_path = Path(output_file)
                parent_dir = output_path.parent

                # Check if parent directory exists or can be created
                if not parent_dir.exists():
                    try:
                        # Don't actually create it, just check if we can
                        parent_dir.mkdir(parents=True, exist_ok=True)
                        logger.info(f"Created output directory for {stage_name}: {parent_dir}")
                    except Exception as e:
                        raise ValueError(
                            f"Cannot create output directory for {stage_name} stage: {parent_dir}. "
                            f"Error: {e}"
                        )
        return self

    @model_validator(mode='after')
    def validate_alert_channel_configs(self):
        """Validate alert channel configurations if alerting is enabled"""
        if self.alerts.enabled and not self.alerts.channels:
            logger.warning(
                "Alerting is enabled but no channels are configured. "
                "Alerts will only be written to the alert_file."
            )

        # Validate email channel configurations
        for channel in self.alerts.channels:
            if channel.get('type') == 'email':
                required_fields = ['smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'from_addr', 'to_addrs']
                missing = [f for f in required_fields if f not in channel or not channel[f]]
                if missing:
                    raise ValueError(
                        f"Email alert channel is missing required fields: {', '.join(missing)}"
                    )
        return self

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'PipelineConfig':
        """
        Create and validate configuration from dictionary.

        Raises:
            ValueError: If configuration is invalid (includes detailed error messages)
        """
        try:
            return cls(**config_dict)
        except Exception as e:
            # Enhance error message with helpful context
            error_msg = str(e)
            if "extra fields not permitted" in error_msg.lower():
                # Extract the unknown field name from error
                import re
                match = re.search(r"'(\w+)'", error_msg)
                if match:
                    unknown_field = match.group(1)
                    raise ValueError(
                        f"Unknown configuration key '{unknown_field}' detected. "
                        f"This might be a typo. Please check your configuration file. "
                        f"Original error: {error_msg}"
                    ) from e
            raise ValueError(f"Configuration validation failed: {error_msg}") from e

    def to_dict(self) -> Dict[str, Any]:
        """Export configuration as dictionary"""
        return self.model_dump()