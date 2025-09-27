import hashlib
import posixpath
from typing import Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from w3lib.url import canonicalize_url


def sha1_hash(url: str) -> str:
    """Generate a SHA-1 hex digest for the provided URL string."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _normalize_url_components(url: str, lowercase_path: bool = False) -> str:
    """Return canonicalized URL components, optionally lowercasing the path."""
    canonical = canonicalize_url(url)

    if not lowercase_path:
        return canonical

    parsed = urlparse(canonical)
    lowered_path = parsed.path.lower() if parsed.path else parsed.path

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            lowered_path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


def _normalize_for_hash(url: str) -> str:
    """Normalize a URL specifically for hashing comparisons."""
    if url is None:
        raise TypeError("url must not be None")

    original = urlparse(url)
    canonical = canonicalize_url(url)
    parsed = urlparse(canonical)

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port

    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"

    normalized_path = posixpath.normpath(path)
    if normalized_path == ".":
        normalized_path = "/"

    if parsed.path.endswith("/") and not normalized_path.endswith("/") and normalized_path != "/":
        normalized_path += "/"

    normalized_path = normalized_path.lower()

    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs), doseq=True) if query_pairs else ""

    fragment = original.fragment

    return urlunparse((scheme, netloc, normalized_path, "", query, fragment))


def canonicalize_and_hash(url: str) -> Tuple[str, str]:
    """Return the canonical URL and a normalized SHA-1 hash."""
    if url is None:
        raise TypeError("url must not be None")

    canonical_url = canonicalize_url(url)
    normalized_for_hash = _normalize_for_hash(url)
    url_hash = sha1_hash(normalized_for_hash)

    return canonical_url, url_hash


def is_valid_uconn_url(url: str) -> bool:
    """Check if URL is a valid UConn domain URL"""
    if url is None:
        raise TypeError("url must not be None")

    try:
        canonicalize_url(url)
        parsed = urlparse(_normalize_for_hash(url))

        # Only allow HTTP/HTTPS schemes
        if parsed.scheme not in ('http', 'https'):
            return False

        # Extract domain and check for valid UConn domains
        domain = (parsed.hostname or "").lower()

        # Must end with uconn.edu or be exactly uconn.edu
        return domain == 'uconn.edu' or domain.endswith('.uconn.edu')

    except Exception:
        return False


def normalize_url(url: str) -> str:
    """Normalize URL for consistent processing"""
    return canonicalize_url(url)


def extract_domain(url: str) -> str:
    """Extract domain from URL"""
    parsed = urlparse(_normalize_for_hash(url))
    return (parsed.hostname or "").lower()
