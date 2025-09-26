import hashlib
import posixpath
from typing import Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from w3lib.url import canonicalize_url


def sha1_hash(url: str) -> str:
    """Generate SHA-1 hash for a URL"""
    return hashlib.sha1(url.encode('utf-8')).hexdigest()



def _normalize_url_components(url: str, lowercase_path: bool = False) -> str:
    """Normalize URL components with full path traversal resolution.

    Args:
        url: The URL to normalize
        lowercase_path: If True, convert path to lowercase for case-insensitive comparison

    Returns:
        Fully normalized URL with path traversal resolved
    """
    parsed = urlparse(url)

    # Normalize scheme and hostname to lowercase
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port

    # Remove default ports
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    # Normalize path: handle empty paths differently for canonical vs hash
    path = parsed.path
    has_empty_path = not path

    # For empty paths, preserve original behavior (no trailing slash for canonical)
    if not path:
        path = ""
    elif not path.startswith("/"):
        path = f"/{path}"

    # Convert path to lowercase if requested (for hashing)
    if lowercase_path:
        path = path.lower()

    # Handle empty path case - preserve original empty path (don't add trailing slash)
    if has_empty_path:
        normalised_path = ""
    else:
        # Fully resolve path traversal using normpath
        normalised_path = posixpath.normpath(path)

        # Handle edge case where normpath returns "."
        if normalised_path == ".":
            normalised_path = "/"

        # Preserve trailing slash semantics from original path
        if path.endswith("/") and not normalised_path.endswith("/") and normalised_path != "/":
            normalised_path += "/"

    # Normalize query parameters: sort them for consistent ordering
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs), doseq=True) if query_pairs else ""

    # Remove fragment (like w3lib canonicalize_url does)
    fragment = ""

    return urlunparse((scheme, netloc, normalised_path, "", query, fragment))


def _normalize_for_hash(url: str) -> str:
    """Normalize URL components for hashing with case-insensitive path and preserved fragments."""
    parsed = urlparse(url)

    # Normalize scheme and hostname to lowercase
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port

    # Remove default ports
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    # Normalize path with case-insensitive handling
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"

    # Convert path to lowercase for case-insensitive hashing
    path = path.lower()

    # Fully resolve path traversal using normpath
    normalised_path = posixpath.normpath(path)

    # Handle edge case where normpath returns "."
    if normalised_path == ".":
        normalised_path = "/"

    # Preserve trailing slash semantics from original path
    original_path = parsed.path or "/"
    if original_path.endswith("/") and not normalised_path.endswith("/") and normalised_path != "/":
        normalised_path += "/"

    # Normalize query parameters: sort them for consistent ordering
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    query = urlencode(sorted(query_pairs), doseq=True) if query_pairs else ""

    # Preserve fragment for hash collision resistance
    fragment = parsed.fragment

    return urlunparse((scheme, netloc, normalised_path, "", query, fragment))


def canonicalize_and_hash(url: str) -> Tuple[str, str]:
    """Canonicalize URL and return (canonical_url, hash).

    Returns normalized string for both canonical URL and hash input.
    Both use path-traversal resolution with case-insensitive hashing.
    """
    # Use our custom normalization for the canonical URL
    canonical_url = _normalize_url_components(url, lowercase_path=False)

    # Use case-insensitive normalization for hashing
    normalized_for_hash = _normalize_for_hash(url)
    url_hash = sha1_hash(normalized_for_hash)

    return canonical_url, url_hash


def is_valid_uconn_url(url: str) -> bool:
    """Check if URL is a valid UConn domain URL"""
    if url is None:
        raise TypeError("url must not be None")

    try:
        canonical_url = canonicalize_url(url)
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
