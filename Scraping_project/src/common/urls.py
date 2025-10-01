# TODO: Add support for more flexible URL normalization, such as allowing the user to specify which parts of the URL to normalize.
import posixpath
from urllib.parse import urlparse, urlunparse

from w3lib.url import canonicalize_url


def _sanitize_path(path: str) -> str:
    """Resolve dot segments while preserving leading/trailing slash semantics."""
    if not path:
        return ""

    has_trailing = path.endswith("/")
    candidate = path if path.startswith("/") else f"/{path}"

    normalized = posixpath.normpath(candidate)
    if normalized == ".":
        normalized = "/"

    if has_trailing and normalized != "/" and not normalized.endswith("/"):
        normalized += "/"

    if not normalized.startswith("/"):
        normalized = f"/{normalized}"

    return normalized


def normalize_url(url: str) -> str:
    """Canonicalize a URL with path traversal resolution."""
    if url is None:
        raise TypeError("url must not be None")

    parsed = urlparse(url)
    sanitized_path = _sanitize_path(parsed.path)

    sanitized = urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc,
            sanitized_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )

    canonical_temp = canonicalize_url(sanitized)
    parsed_canonical = urlparse(canonical_temp)

    netloc = parsed_canonical.netloc
    default_ports = {"http": 80, "https": 443}
    scheme_lower = parsed_canonical.scheme.lower()

    if parsed_canonical.port and default_ports.get(scheme_lower) == parsed_canonical.port:
        userinfo = ""
        if parsed_canonical.username:
            userinfo = parsed_canonical.username
            if parsed_canonical.password:
                userinfo += f":{parsed_canonical.password}"
            userinfo += "@"

        host = parsed_canonical.hostname or ""
        netloc = f"{userinfo}{host}"

    canonical = urlunparse(
        (
            parsed_canonical.scheme,
            netloc,
            parsed_canonical.path,
            parsed_canonical.params,
            parsed_canonical.query,
            parsed_canonical.fragment,
        )
    )

    if not parsed.path and canonical.endswith("/"):
        canonical = canonical[:-1]

    return canonical




def canonicalize_url_simple(url: str) -> str:
    """Return just the canonical URL without hashing."""
    return normalize_url(url)


def is_valid_uconn_url(url: str) -> bool:
    # TODO: This URL validation is specific to UConn. It should be made more generic to support other domains.
    """Check if URL is a valid UConn domain URL"""
    if url is None:
        raise TypeError("url must not be None")

    try:
        canonicalize_url(url)
        parsed = urlparse(normalize_url(url))

        # Only allow HTTP schemes
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
