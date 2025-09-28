import posixpath
import re
from urllib.parse import urlparse, urlunparse

from w3lib.url import canonicalize_url


def _sanitize_path(path: str) -> str:
    """Resolve dot segments while preserving leading/trailing slash semantics."""
    if not path:
        return ""

    candidate = str(path)
    has_trailing = candidate.endswith("/")

    candidate = re.sub(r"/+", "/", candidate)

    if not candidate.startswith("/"):
        candidate = f"/{candidate}"

    normalized = posixpath.normpath(candidate)
    if normalized == ".":
        normalized = "/"

    if has_trailing and normalized != "/" and not normalized.endswith("/"):
        normalized += "/"

    return normalized


def normalize_url(url: str) -> str:
    """Canonicalize a URL with path traversal resolution."""
    if url is None:
        raise TypeError("url must not be None")

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("url must include a scheme and host")

    scheme = parsed.scheme.lower()
    sanitized_path = _sanitize_path(parsed.path)
    if not parsed.path and sanitized_path == "/":
        sanitized_path = ""

    default_ports = {"http": 80, "https": 443}
    netloc = parsed.netloc

    if parsed.port and default_ports.get(scheme) == parsed.port:
        port_suffix = f":{parsed.port}"
        at_index = netloc.rfind("@")
        suffix_index = netloc.rfind(port_suffix)
        if suffix_index != -1 and suffix_index > at_index:
            netloc = netloc[:suffix_index]

    normalized = urlunparse(
        (
            scheme,
            netloc,
            sanitized_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )

    canonical = canonicalize_url(normalized, keep_fragments=True)

    return canonical




def canonicalize_url_simple(url: str) -> str:
    """Return just the canonical URL without hashing."""
    return normalize_url(url)


def is_valid_uconn_url(url: str) -> bool:
    """Check if URL is a valid UConn domain URL"""
    if url is None:
        raise TypeError("url must not be None")

    try:
        canonicalize_url(url)
        parsed = urlparse(normalize_url(url))

        # Only allow HTTP/HTTPS schemes
        if parsed.scheme not in ('http', 'https'):
            return False

        # Extract domain and check for valid UConn domains
        domain = (parsed.hostname or "").lower()

        # Must end with uconn.edu or be exactly uconn.edu
        return domain == 'uconn.edu' or domain.endswith('.uconn.edu')

    except Exception:
        return False


def extract_domain(url: str) -> str:
    """Extract domain from URL"""
    parsed = urlparse(normalize_url(url))
    return (parsed.hostname or "").lower()
