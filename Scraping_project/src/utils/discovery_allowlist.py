"""Shared ``stage1.allowed_domains`` for sitemap discovery and SeedManager (#940).

Sitemap indexes that point at another domain, and sitemap URLs that redirect
there (or loop), must be dropped before those locs become seeds. SeedManager
applies the same list again when the spider source is a sitemap walk or
rustmapper handoff.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import urlparse

try:
    from prometheus_client import Counter

    CROSS_DOMAIN_SITEMAP_DROPPED: Any = Counter(
        "cross_domain_sitemap_dropped",
        "Sitemap documents and sitemap seeds dropped because the host left the "
        "shared stage1 allowlist, or a sitemap redirect looped.",
    )
except Exception:  # prometheus_client missing or metric already registered
    CROSS_DOMAIN_SITEMAP_DROPPED = None

# Sources whose seeds are sitemap discovery output, not page-link expansion.
DISCOVERY_SEED_SOURCES = frozenset({"sitemap_parser", "rustmapper", "sitemap"})


def record_cross_domain_drop(amount: int = 1) -> None:
    if CROSS_DOMAIN_SITEMAP_DROPPED is None or amount <= 0:
        return
    CROSS_DOMAIN_SITEMAP_DROPPED.inc(amount)


def normalize_domains(domains: Iterable[str] | str | None) -> tuple[str, ...]:
    if not domains:
        return ()
    if isinstance(domains, str):
        domains = [domains]
    out = []
    for raw in domains:
        domain = str(raw).strip().lower().lstrip(".")
        if domain.startswith("*."):
            domain = domain[2:]
        if domain:
            out.append(domain)
    return tuple(dict.fromkeys(out))


def host_in_domains(host: str, domains: Iterable[str]) -> bool:
    """Exact-or-subdomain host match (``www.uconn.edu`` yes, ``uconn.edu.evil.com`` no)."""
    host = (host or "").strip().lower().rstrip(".")
    return bool(host) and any(host == domain or host.endswith("." + domain) for domain in domains)


def _config_value(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        try:
            from src.core.config import get_config

            config = get_config()
        except Exception:
            return default
    for prefix in ("stage1", "stages.stage1"):
        try:
            value = config.get(f"{prefix}.{key}")
        except Exception:
            value = None
        if value is not None:
            return value
    return default


def allowed_domains_from_config(config: Any = None) -> tuple[str, ...]:
    """``stage1.allowed_domains``, shared by the sitemap seeder and SeedManager."""
    return normalize_domains(_config_value(config, "allowed_domains", ()))


def effective_sitemap_allowlist(base_url: str, configured: Iterable[str] | None) -> tuple[str, ...]:
    """Allowlist for one sitemap walk.

    When the seed host is inside the configured list, the whole shared list
    applies. Otherwise the walk stays on the seed host, so a unit crawl of an
    unlisted host still parses its own tree and still drops every other host.
    """
    domains = normalize_domains(configured)
    host = (urlparse(base_url).hostname or "").strip().lower().rstrip(".")
    if domains and host and host_in_domains(host, domains):
        return domains
    if host:
        return normalize_domains((host,))
    return domains


def url_host_allowed(url: str, domains: Iterable[str]) -> bool:
    host = urlparse(url).hostname or ""
    if not host:
        return True  # relative loc; resolved against the current sitemap
    return host_in_domains(host, domains)
