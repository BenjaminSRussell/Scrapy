import hashlib
from typing import Tuple
from w3lib.url import canonicalize_url


def sha1_hash(url: str) -> str:
    """Generate SHA-1 hash for a URL"""
    return hashlib.sha1(url.encode('utf-8')).hexdigest()


def canonicalize_and_hash(url: str) -> Tuple[str, str]:
    """Canonicalize URL and return (canonical_url, hash)"""
    canonical_url = canonicalize_url(url)
    url_hash = sha1_hash(canonical_url)
    return canonical_url, url_hash


def is_valid_uconn_url(url: str) -> bool:
    """Check if URL is a valid UConn domain URL"""
    canonical_url = canonicalize_url(url)
    return "uconn.edu" in canonical_url.lower()


def normalize_url(url: str) -> str:
    """Normalize URL for consistent processing"""
    return canonicalize_url(url)


def extract_domain(url: str) -> str:
    """Extract domain from URL"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.netloc.lower()