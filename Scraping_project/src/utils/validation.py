"""
Global validation utilities.

Provides input validation functions used throughout the pipeline.
``validate_startup_config`` schema-checks config.yml before CLI and
orchestrator startup (#237).
"""

import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)


MAX_URL_LENGTH = 2048

# Stable rejection codes returned by url_rejection_reason() (#265). Callers log
# and count these; do not rename them.
URL_NOT_A_STRING = "not_a_string"
URL_EMPTY = "empty"
URL_TOO_LONG = "too_long"
URL_UNPARSABLE = "unparsable"
URL_BAD_SCHEME = "bad_scheme"
URL_NO_HOST = "no_host"
URL_WHITESPACE = "whitespace"
URL_REJECTION_CODES = frozenset({
    URL_NOT_A_STRING, URL_EMPTY, URL_TOO_LONG, URL_UNPARSABLE, URL_BAD_SCHEME, URL_NO_HOST, URL_WHITESPACE,
})


def url_rejection_reason(url: object) -> Optional[str]:
    """Why ``url`` is not a crawlable http(s) URL, as a stable code, or None if it is.

    Codes (see ``URL_REJECTION_CODES``): ``not_a_string``, ``empty``,
    ``too_long`` (>= 2048 chars), ``whitespace`` (spaces/control chars inside),
    ``unparsable``, ``bad_scheme`` (not http/https), ``no_host``.
    """
    if not isinstance(url, str):
        return URL_NOT_A_STRING
    if not url.strip():
        return URL_EMPTY
    if len(url) >= MAX_URL_LENGTH:
        return URL_TOO_LONG
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url):
        return URL_WHITESPACE
    try:
        result = urlparse(url)
        result.port  # raises ValueError for a non-numeric / out-of-range port
        host = result.hostname
    except ValueError as e:
        logger.debug(f"URL validation failed for {url!r}: {e}")
        return URL_UNPARSABLE
    if result.scheme not in ("http", "https"):
        return URL_BAD_SCHEME
    if not host:  # "http://", "http://:80", "http://user@"
        return URL_NO_HOST
    return None


def is_valid_url(url: str) -> bool:
    """
    Validate URL format.

    Args:
        url: URL string to validate

    Returns:
        True if URL is valid, False otherwise (``url_rejection_reason`` says why)

    Example:
        if is_valid_url("https://uconn.edu"):
            process_url(url)
    """
    return url_rejection_reason(url) is None


def is_uconn_domain(url: str) -> bool:
    """
    Check if URL is from UConn domain.

    Args:
        url: URL string to check

    Returns:
        True if URL is from uconn.edu domain, False otherwise

    Example:
        if is_uconn_domain("https://uconn.edu/page"):
            # Process UConn URL
    """
    if not is_valid_url(url):
        return False

    try:
        # Exact host or a subdomain (#757): the old substring test accepted
        # notuconn.edu and uconn.edu.attacker.example.
        host = (urlparse(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    return host == "uconn.edu" or host.endswith(".uconn.edu")


def sanitize_text(text: str, max_length: Optional[int] = None) -> str:
    """
    Sanitize text input.

    - Removes excessive whitespace
    - Truncates to max_length if specified
    - Strips leading/trailing whitespace

    Args:
        text: Text to sanitize
        max_length: Optional maximum length

    Returns:
        Sanitized text

    Example:
        clean = sanitize_text("  Hello   World  ", max_length=100)
    """
    if not text or not isinstance(text, str):
        return ""

    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Truncate if needed (max_length=0 means empty, not "no limit"; #757)
    if max_length is not None and len(text) > max(0, max_length):
        text = text[:max(0, max_length)].rstrip()

    return text


def validate_stage_data(data: dict, required_fields: list) -> bool:
    """
    Validate stage data has required fields.

    Args:
        data: Dictionary to validate
        required_fields: List of required field names

    Returns:
        True if all required fields present, False otherwise

    Example:
        required = ["url", "title", "word_count"]
        if validate_stage_data(page_data, required):
            process_page(page_data)
    """
    if not isinstance(data, dict):
        return False

    return all(field in data for field in required_fields)


def is_safe_filename(filename: str) -> bool:
    """
    Check if filename is safe (no path traversal, etc).

    Args:
        filename: Filename to check

    Returns:
        True if filename is safe, False otherwise

    Example:
        if is_safe_filename(user_input):
            save_file(user_input)
    """
    if not filename or not isinstance(filename, str):
        return False

    # Check for path traversal attempts ("." is the directory itself; #757)
    if filename == '.' or '..' in filename or '/' in filename or '\\' in filename:
        return False

    # Check for reasonable length
    if len(filename) > 255:
        return False

    # Check for allowed characters (alphanumeric, dash, underscore, dot)
    if not re.match(r'^[a-zA-Z0-9_.-]+$', filename):
        return False

    return True


def normalize_url(url: str) -> str:
    """Canonical URL for comparison and storage (#728).

    Delegates to ``src.utils.url_canon.canonicalize_url`` so validation, Redis,
    spiders and the lake agree; non-http(s) input is returned unchanged.

    Example:
        normalize_url("https://UConn.EDU/Page/?utm_source=email#section")
        # Returns: "https://uconn.edu/page"
    """
    from src.utils.url_canon import canonical_or_raw

    return canonical_or_raw(url)


_TLD_EXTRACT = None


def _tld_extractor():
    """Offline public-suffix extractor (bundled snapshot; never hits the network)."""
    global _TLD_EXTRACT
    if _TLD_EXTRACT is None:
        import tldextract  # a Scrapy dependency, always installed

        # Private suffixes too (github.io, blogspot.com...): each site is its own key.
        _TLD_EXTRACT = tldextract.TLDExtract(
            suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
        )
    return _TLD_EXTRACT


def registrable_domain(url_or_host: str) -> str:
    """Public-suffix-aware registrable domain ("eTLD+1") for a URL or hostname (#251).

    This is the Delta partition key for stage1_discovery/stage2_page_analysis.
    It deliberately differs from ``extract_domain`` (the full host): partitions
    group all of a site's subdomains, so ``www.cs.uconn.edu`` and ``uconn.edu``
    share ``uconn.edu``, and ``news.bbc.co.uk`` is ``bbc.co.uk``, not ``co.uk``.

    IP addresses and single-label hosts (``localhost``) are returned as-is;
    empty or unparsable input returns ``"unknown"``.
    """
    import ipaddress

    value = (url_or_host or "").strip()
    if not value:
        return "unknown"
    try:
        host = urlparse(value).hostname if "://" in value else value.split("/", 1)[0].split(":", 1)[0]
    except ValueError:
        return "unknown"
    host = (host or "").strip(".").lower()
    if not host:
        return "unknown"
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host.strip("[]")
    except ValueError:
        pass
    parts = _tld_extractor()(host)
    if parts.domain and parts.suffix:
        return f"{parts.domain}.{parts.suffix}"
    return host  # e.g. localhost, intranet names, bare suffixes


def extract_domain(url: str) -> str:
    """
    Extract domain from URL.

    Returns the full host (netloc). For the registrable domain used as the
    Delta partition key, see ``registrable_domain``.

    Args:
        url: URL to extract domain from

    Returns:
        Domain name, or empty string if invalid

    Example:
        domain = extract_domain("https://www.uconn.edu/page")
        # Returns: "www.uconn.edu"
    """
    if not is_valid_url(url):
        return ""

    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# config.yml schema (#237)
# ---------------------------------------------------------------------------
#
# Redis and Kafka are strict: a wrong type (redis.port: "6379") or an unknown
# key (a typo) fails startup. Every other top-level section is allowed through
# so stage settings can grow without a schema edit. Dotted Kafka producer keys
# are renamed before validation; unknown dotted keys are left as-is so
# extra=forbid reports the typo.

_PRODUCER_DOT_KEYS = {
    "compression.type": "compression_type",
    "enable.idempotence": "enable_idempotence",
}

_STRICT = ConfigDict(extra="forbid")


class ConfigValidationError(ValueError):
    """config.yml failed schema validation. ``str(error)`` lists field paths."""


class RedisSection(BaseModel):
    model_config = _STRICT
    host: StrictStr
    port: StrictInt
    db: StrictInt
    password: StrictStr | None = None
    max_connections: StrictInt


class PostgresSection(BaseModel):
    model_config = _STRICT
    host: StrictStr
    port: StrictInt
    database: StrictStr
    user: StrictStr
    password: StrictStr | None = None


class KafkaTopicSettings(BaseModel):
    """Per-topic create options. Only the keys present in config.yml are set."""

    model_config = _STRICT
    partitions: StrictInt | None = None
    retention_ms: StrictInt | None = None
    replication_factor: StrictInt | None = None


class KafkaProducer(BaseModel):
    model_config = _STRICT
    compression_type: StrictStr
    acks: StrictStr | StrictInt
    enable_idempotence: StrictBool
    retries: StrictInt

    @model_validator(mode="before")
    @classmethod
    def _rename_dotted_keys(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        return {_PRODUCER_DOT_KEYS.get(str(key), key): value for key, value in data.items()}


class KafkaConsumer(BaseModel):
    model_config = _STRICT
    group_id: StrictStr
    auto_offset_reset: StrictStr
    enable_auto_commit: StrictBool
    auto_commit_interval_ms: StrictInt


class KafkaSection(BaseModel):
    model_config = _STRICT
    bootstrap_servers: StrictStr
    topics: dict[StrictStr, StrictStr]
    topic_settings: dict[StrictStr, KafkaTopicSettings]
    producer: KafkaProducer
    consumer: KafkaConsumer
    message_key_field: StrictStr
    require_idempotence: StrictBool


def _non_negative_or_null(value: object) -> object:
    """Budget caps are null (uncapped) or a finite number >= 0. Booleans are rejected."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a number or null")
    if value != value or value == float("inf") or value == float("-inf"):
        raise ValueError("must be a finite number or null")
    if value < 0:
        raise ValueError("must be >= 0")
    return value


class CostControlsSection(BaseModel):
    """Daily Stage 3/4 budgets (#528). Null fields, or a null block, are uncapped."""

    model_config = _STRICT
    stage3_max_gpu_minutes_per_day: float | None = None
    stage4_max_gpu_minutes_per_day: float | None = None
    stage4_max_ocr_pages_per_day: float | None = None

    @field_validator(
        "stage3_max_gpu_minutes_per_day",
        "stage4_max_gpu_minutes_per_day",
        "stage4_max_ocr_pages_per_day",
        mode="before",
    )
    @classmethod
    def _cap_is_number_or_null(cls, value: object) -> object:
        return _non_negative_or_null(value)


class PipelineConfig(BaseModel):
    """Top-level config.yml. Unknown sections are ignored; redis, kafka, and cost_controls are not."""

    model_config = ConfigDict(extra="allow")
    redis: RedisSection
    kafka: KafkaSection
    postgres: PostgresSection | None = None
    cost_controls: CostControlsSection | None = None


def default_config_path() -> Path:
    """Scraping_project/config.yml, next to this package (not the process cwd)."""
    return Path(__file__).resolve().parents[2] / "config.yml"


def _field_path(loc: object) -> str:
    if not isinstance(loc, tuple) or not loc:
        return "<root>"
    return ".".join(str(part) for part in loc)


def _format_validation_error(exc: ValidationError) -> str:
    lines = ["Invalid config.yml:"]
    for err in exc.errors():
        got = err.get("input")
        lines.append(f"  {_field_path(err.get('loc'))}: {err.get('msg', 'invalid')} (got {type(got).__name__})")
    return "\n".join(lines)


def validate_config_document(data: object) -> None:
    """Raise ``ConfigValidationError`` with field paths when ``data`` is not a valid config.yml.

    Does not read the filesystem and does not apply defaults. A string ``redis.port``
    fails; the shipped config.yml passes.
    """
    if not isinstance(data, dict):
        raise ConfigValidationError(
            f"Invalid config.yml:\n  <root>: top level must be a mapping (got {type(data).__name__})"
        )
    try:
        PipelineConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigValidationError(_format_validation_error(exc)) from exc


def validate_startup_config(path: Path | None = None) -> None:
    """Validate config.yml before CLI or orchestrator startup (#237).

    A missing file is left to ``src.core.config`` defaults (same as today).
    A present file that does not match the schema raises ``ConfigValidationError``
    instead of failing later inside a worker.
    """
    config_path = default_config_path() if path is None else Path(path)
    if not config_path.is_file():
        return
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigValidationError(f"Invalid config.yml:\n  <root>: YAML parse error ({exc})") from exc
    validate_config_document(loaded)
