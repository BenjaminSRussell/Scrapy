"""
UConn.edu domain filtering to ensure we ONLY scrape university content.

This module provides strict domain filtering to prevent crawling outside
the UConn.edu domain and its approved subdomains.
"""

import logging
from typing import List
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# STRICT: Only UConn.edu and approved subdomains
ALLOWED_DOMAINS = [
    'uconn.edu',
    'www.uconn.edu',
]

# Approved UConn subdomains (extend as needed)
APPROVED_SUBDOMAINS = [
    'admissions.uconn.edu',
    'registrar.uconn.edu',
    'lib.uconn.edu',
    'today.uconn.edu',
    'research.uconn.edu',
    'catalog.uconn.edu',
    'studentadmin.uconn.edu',
    'hr.uconn.edu',
    'huskyct.uconn.edu',
    'grad.uconn.edu',
    'undergrad.uconn.edu',
    'athletics.uconn.edu',
    'financialaid.uconn.edu',
    'housing.uconn.edu',
    'dining.uconn.edu',
    'involvement.uconn.edu',
    'health.uconn.edu',
]

# Combine all allowed domains
ALL_ALLOWED_DOMAINS = ALLOWED_DOMAINS + APPROVED_SUBDOMAINS


def is_uconn_url(url: str, strict: bool = True) -> bool:
    """
    Check if URL belongs to UConn.edu domain.

    Args:
        url: URL to check
        strict: If True, only allow explicitly approved subdomains.
                If False, allow all *.uconn.edu subdomains.

    Returns:
        True if URL is from UConn.edu, False otherwise

    Examples:
        >>> is_uconn_url('https://uconn.edu')
        True
        >>> is_uconn_url('https://admissions.uconn.edu')
        True
        >>> is_uconn_url('https://evil.com')
        False
        >>> is_uconn_url('https://random.uconn.edu', strict=True)
        False
        >>> is_uconn_url('https://random.uconn.edu', strict=False)
        True
    """
    try:
        domain = urlparse(url).netloc.lower()

        # Exact match with allowed domains
        if domain in ALL_ALLOWED_DOMAINS:
            return True

        # If not strict, allow all *.uconn.edu subdomains
        if not strict and domain.endswith('.uconn.edu'):
            return True

        return False

    except Exception as e:
        logger.warning(f"Error parsing URL {url}: {e}")
        return False


def filter_uconn_urls(urls: List[str], strict: bool = True) -> List[str]:
    """
    Filter list to only include UConn URLs.

    Args:
        urls: List of URLs to filter
        strict: If True, only allow approved subdomains

    Returns:
        Filtered list containing only UConn URLs

    Example:
        >>> urls = ['https://uconn.edu', 'https://evil.com', 'https://lib.uconn.edu']
        >>> filter_uconn_urls(urls)
        ['https://uconn.edu', 'https://lib.uconn.edu']
    """
    filtered = [url for url in urls if is_uconn_url(url, strict=strict)]

    removed_count = len(urls) - len(filtered)
    if removed_count > 0:
        logger.debug(f"Filtered out {removed_count} non-UConn URLs")

    return filtered


def get_allowed_domains() -> List[str]:
    """
    Get list of all allowed UConn domains.

    Returns:
        List of allowed domain strings
    """
    return ALL_ALLOWED_DOMAINS.copy()


def add_approved_subdomain(subdomain: str):
    """
    Add a new approved subdomain to the allowed list.

    Args:
        subdomain: Full subdomain (e.g., 'newsite.uconn.edu')

    Raises:
        ValueError: If subdomain is not a uconn.edu subdomain
    """
    subdomain = subdomain.lower()

    if not subdomain.endswith('.uconn.edu') and subdomain != 'uconn.edu':
        raise ValueError(f"Subdomain must end with .uconn.edu: {subdomain}")

    if subdomain not in APPROVED_SUBDOMAINS:
        APPROVED_SUBDOMAINS.append(subdomain)
        ALL_ALLOWED_DOMAINS.append(subdomain)
        logger.info(f"Added approved subdomain: {subdomain}")


def extract_domain(url: str) -> str:
    """
    Extract domain from URL.

    Args:
        url: URL to extract domain from

    Returns:
        Domain string (e.g., 'uconn.edu')

    Example:
        >>> extract_domain('https://admissions.uconn.edu/apply')
        'admissions.uconn.edu'
    """
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ''


def is_same_domain(url1: str, url2: str) -> bool:
    """
    Check if two URLs are from the same domain.

    Args:
        url1: First URL
        url2: Second URL

    Returns:
        True if both URLs have the same domain
    """
    return extract_domain(url1) == extract_domain(url2)


# Statistics tracking
class DomainStats:
    """Track domain filtering statistics."""

    def __init__(self):
        self.total_checked = 0
        self.allowed = 0
        self.blocked = 0
        self.blocked_domains = {}

    def record_check(self, url: str, allowed: bool):
        """Record a domain check."""
        self.total_checked += 1

        if allowed:
            self.allowed += 1
        else:
            self.blocked += 1
            domain = extract_domain(url)
            self.blocked_domains[domain] = self.blocked_domains.get(domain, 0) + 1

    def get_summary(self) -> dict:
        """Get statistics summary."""
        return {
            'total_checked': self.total_checked,
            'allowed': self.allowed,
            'blocked': self.blocked,
            'block_rate': self.blocked / max(self.total_checked, 1),
            'top_blocked_domains': sorted(
                self.blocked_domains.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]
        }


# Global stats instance
_stats = DomainStats()


def get_domain_stats() -> dict:
    """Get current domain filtering statistics."""
    return _stats.get_summary()


def is_uconn_url_tracked(url: str, strict: bool = True) -> bool:
    """
    Check if URL is UConn with statistics tracking.

    Args:
        url: URL to check
        strict: If True, only allow approved subdomains

    Returns:
        True if URL is from UConn.edu
    """
    result = is_uconn_url(url, strict=strict)
    _stats.record_check(url, result)
    return result
