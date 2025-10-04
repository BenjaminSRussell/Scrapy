import posixpath
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

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


def normalize_url(url: str, lowercase_path_query: bool = True) -> str:
    """
    Canonicalize a URL with enhanced normalization rules.

    - Resolves path traversal (e.g., /a/b/../c -> /a/c)
    - Removes default ports (e.g., :80, :443)
    - Removes URL fragments (e.g., #section)
    - Sorts query parameters for consistency
    - Optionally lowercases the path and query
    """
    if url is None:
        raise TypeError("url must not be None")

    # Use w3lib's canonicalize_url for initial cleaning (removes fragments, etc.)
    canonical_temp = canonicalize_url(url)
    parsed = urlparse(canonical_temp)

    # Sanitize path to resolve dot segments
    sanitized_path = _sanitize_path(parsed.path)

    # Sort query parameters
    query_params = parse_qsl(parsed.query)
    sorted_query = sorted(query_params, key=lambda x: x[0])
    encoded_query = urlencode(sorted_query)

    # Optionally lowercase path and query
    if lowercase_path_query:
        sanitized_path = sanitized_path.lower()
        encoded_query = encoded_query.lower()

    # Reconstruct the URL
    canonical = urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            sanitized_path,
            parsed.params,
            encoded_query,
            '',  # Explicitly remove fragment
        )
    )

    # Remove trailing slash from root path
    if parsed.path in ('', '/') and canonical.endswith('/'):
        canonical = canonical[:-1]

    return canonical



def canonicalize_url_simple(url: str) -> str:
    """Return just the canonical URL without hashing."""
    return normalize_url(url)


def is_valid_uconn_url(url: str) -> bool:
    
    """Check if URL is a valid UConn domain URL"""
    if url is None:
        raise TypeError("url must not be None")

    try:
        # Use the robust normalize_url
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
