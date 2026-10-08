"""
Global validation utilities.

Provides input validation functions used throughout the pipeline.
"""

from typing import Optional
from urllib.parse import urlparse
import re
import logging

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

    # Exact host or a subdomain. The old substring test on netloc accepted
    # "uconn.edu.evil.com", "notuconn.edu" and "https://uconn.edu@evil.com".
    host = (urlparse(url).hostname or "").rstrip(".").lower()
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

    # Truncate if needed
    if max_length and len(text) > max_length:
        text = text[:max_length]

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

    # Check for path traversal attempts
    if '..' in filename or '/' in filename or '\\' in filename:
        return False

    # Check for reasonable length
    if len(filename) > 255:
        return False

    # Check for allowed characters (alphanumeric, dash, underscore, dot)
    if not re.match(r'^[a-zA-Z0-9_.-]+$', filename):
        return False

    return True


def normalize_url(url: str) -> str:
    """
    Normalize URL for consistent comparison.

    - Converts to lowercase
    - Removes trailing slash
    - Removes fragment (#)
    - Removes common tracking parameters

    Args:
        url: URL to normalize

    Returns:
        Normalized URL

    Example:
        normalized = normalize_url("https://UConn.EDU/Page/?utm_source=email#section")
        # Returns: "https://uconn.edu/page"
    """
    if not is_valid_url(url):
        return url

    try:
        parsed = urlparse(url.lower())

        # Remove fragment
        normalized = parsed._replace(fragment='')

        # Remove trailing slash from path
        path = normalized.path.rstrip('/')

        # Remove common tracking parameters
        query_params = []
        if normalized.query:
            for param in normalized.query.split('&'):
                key = param.split('=')[0]
                # Skip common tracking parameters
                if not key.startswith(('utm_', 'ref', 'source', 'campaign')):
                    query_params.append(param)

        query = '&'.join(query_params) if query_params else ''

        normalized = normalized._replace(path=path, query=query)

        return normalized.geturl()
    except Exception as e:
        logger.debug(f"URL normalization failed for {url}: {e}")
        return url


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
