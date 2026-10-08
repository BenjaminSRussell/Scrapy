"""SSRF guard for every URL the pipeline downloads or queues (#682).

A crawler that follows attacker-controlled links, seeds or redirects must not
reach loopback, private networks, link-local / cloud metadata endpoints
(169.254.169.254, metadata.google.internal) or in-cluster service names
(``redis``, ``kafka``, ``postgres``). ``ssrf_block_reason`` decides without any
network I/O unless ``resolve=True``; callers reject before a request is made.

Blocked:
- non-http(s) schemes and URLs with embedded credentials;
- IP literals that are not globally routable, including legacy IPv4 spellings
  that resolvers accept (``2130706433``, ``0x7f000001``, ``0177.0.0.1``,
  ``127.1``), bracketed IPv6 (``[::1]``, ``[fe80::1%eth0]``) and IPv4-mapped
  IPv6 (``[::ffff:127.0.0.1]``);
- ``localhost`` aliases, ``*.localhost`` / ``*.internal`` / ``*.local`` and
  single-label hostnames (Docker/Kubernetes service names);
- with ``resolve=True``: hostnames whose DNS answers include a non-global IP.

``SSRF_ALLOWED_HOSTS`` (comma-separated hostnames, IPs or CIDRs) is an explicit
escape hatch, e.g. ``127.0.0.1`` for local fixture servers in tests.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from aiohttp.abc import AbstractResolver

ALLOWED_SCHEMES = frozenset({"http", "https"})
BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "metadata",
        "metadata.google.internal",
        "instance-data",
    }
)
BLOCKED_SUFFIXES = (".localhost", ".internal", ".local", ".localdomain")
_NUMERIC_V4 = re.compile(r"^(0x[0-9a-f]*|[0-9]+)(\.(0x[0-9a-f]*|[0-9]+)){0,3}$", re.IGNORECASE)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class SSRFBlocked(Exception):
    """Raised by callers that refuse a URL; ``reason`` is a stable label."""

    def __init__(self, url: str, reason: str):
        super().__init__(f"SSRF guard blocked {url!r}: {reason}")
        self.url = url
        self.reason = reason


def _allowlist(raw: str | None = None) -> tuple[set[str], list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    raw = os.getenv("SSRF_ALLOWED_HOSTS", "") if raw is None else raw
    names: set[str] = set()
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in (e.strip().lower().strip("[]") for e in raw.split(",")):
        if not entry:
            continue
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            names.add(entry.rstrip("."))
    return names, nets


def parse_ip_host(host: str) -> IPAddress | None:
    """IP address for an IP-literal host (any spelling), else None."""
    h = host.strip("[]").split("%", 1)[0]
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        if not _NUMERIC_V4.match(h):
            return None
        try:  # legacy forms glibc/inet_aton accept: decimal, hex, octal, short
            ip = ipaddress.IPv4Address(socket.inet_aton(h))
        except OSError:
            return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def ip_block_reason(ip: IPAddress) -> str | None:
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"  # includes 169.254.169.254 cloud metadata
    if ip.is_unspecified:
        return "unspecified"
    if ip.is_multicast:
        return "multicast"
    if ip.is_private:
        return "private"
    if ip.is_reserved or not ip.is_global:
        return "non_global"  # e.g. 100.64.0.0/10 CGNAT, 192.0.2.0/24
    return None


def _is_allowed(host: str, ip: IPAddress | None, allow: tuple[set[str], list]) -> bool:
    names, nets = allow
    if host in names:
        return True
    return ip is not None and any(ip.version == n.version and ip in n for n in nets)


def ssrf_block_reason(url: str, *, resolve: bool = False, allowed_hosts: str | None = None) -> str | None:
    """Why ``url`` must not be fetched/queued, or None when it is safe."""
    if not isinstance(url, str) or not url:
        return "invalid_url"
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname
        _ = parts.port  # raises ValueError on a malformed/out-of-range port
    except ValueError:
        return "invalid_url"
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return "scheme"
    if parts.username is not None or parts.password is not None:
        return "credentials"
    if not host:
        return "no_host"
    host = host.rstrip(".").lower()
    allow = _allowlist(allowed_hosts)
    ip = parse_ip_host(host)
    if _is_allowed(host, ip, allow):
        return None
    if ip is not None:
        reason = ip_block_reason(ip)
        return f"ip_{reason}" if reason else None
    if _NUMERIC_V4.match(host):
        return "invalid_ip"  # numeric host inet_aton rejects (e.g. 999.1.1.1)
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        return "internal_hostname"
    if "." not in host:
        return "single_label_hostname"  # docker/k8s service names: redis, kafka...
    if resolve:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return None  # unresolvable: the fetch itself fails normally
        for info in infos:
            addr = parse_ip_host(str(info[4][0]))
            if addr is not None and not _is_allowed(host, addr, allow):
                reason = ip_block_reason(addr)
                if reason:
                    return f"dns_{reason}"
    return None


def is_ssrf_safe(url: str, *, resolve: bool = False) -> bool:
    return ssrf_block_reason(url, resolve=resolve) is None


try:
    from prometheus_client import Counter as _Counter

    SSRF_BLOCKED = _Counter(
        "scrapy_ssrf_blocked_total",
        "URLs refused by the SSRF guard before download/queueing, by stage and reason.",
        ["stage", "reason"],
    )
except Exception:  # prometheus_client missing or metric already registered
    SSRF_BLOCKED = None


def count_blocked(stage: str, reason: str) -> None:
    if SSRF_BLOCKED is not None:
        SSRF_BLOCKED.labels(stage=stage, reason=reason.split(":", 1)[0]).inc()


# --- #450: DNS answers and non-aiohttp fetchers --------------------------------
#
# ssrf_block_reason() without resolve=True cannot see a public-looking hostname
# whose DNS answer is internal (attacker-controlled DNS, rebinding). Stage 2
# plugs ``safe_resolver()`` into its aiohttp connector so the check runs on the
# addresses actually connected to; the ASR media downloader (requests) uses
# ``guarded_get``, which checks every hop with resolve=True before connecting.

REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class SSRFResolveBlocked(SSRFBlocked, OSError):
    """A hostname resolved to a non-public address (raised from the resolver).

    Also an OSError so aiohttp surfaces it as the ``os_error`` of a
    ClientConnectorError instead of an unrelated crash.
    """


def resolved_block_reason(host: str, addresses: list[str], *, allowed_hosts: str | None = None) -> str | None:
    """``dns_<reason>`` when any resolved address of ``host`` is non-public, else None."""
    allow = _allowlist(allowed_hosts)
    name = host.rstrip(".").lower()
    if name in allow[0]:
        return None
    for address in addresses:
        ip = parse_ip_host(str(address))
        if ip is None or _is_allowed(name, ip, allow):
            continue
        reason = ip_block_reason(ip)
        if reason:
            return f"dns_{reason}"
    return None


def ssrf_error_from(exc: BaseException) -> SSRFBlocked | None:
    """The SSRFBlocked behind an aiohttp/requests connection error, if any."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, SSRFBlocked):
            return current
        seen.add(id(current))
        current = getattr(current, "os_error", None) or current.__cause__ or current.__context__
    return None


def safe_resolver(stage: str = "stage2") -> "AbstractResolver":
    """aiohttp resolver that refuses non-public DNS answers (connect-time check)."""
    import aiohttp
    from aiohttp.abc import AbstractResolver

    class SSRFSafeResolver(AbstractResolver):
        def __init__(self) -> None:
            self._inner: AbstractResolver | None = None

        async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET) -> list:
            if self._inner is None:
                self._inner = aiohttp.DefaultResolver()  # needs the running loop
            results = await self._inner.resolve(host, port, family)
            reason = resolved_block_reason(host, [r["host"] for r in results])
            if reason:
                count_blocked(stage, reason)
                raise SSRFResolveBlocked(host, reason)
            return results

        async def close(self) -> None:
            if self._inner is not None:
                await self._inner.close()

    return SSRFSafeResolver()


def guarded_get(
    url: str,
    *,
    session: Any = None,
    timeout: float | tuple[float, float] = 30,
    max_redirects: int = 5,
    stage: str = "asr",
    **kwargs: Any,
) -> Any:
    """``requests``-style GET that SSRF-checks (with DNS) the URL and every redirect hop.

    Redirects are followed by hand so a public URL can't bounce the fetch to
    169.254.169.254 or an in-cluster service. Raises SSRFBlocked.
    """
    from urllib.parse import urljoin

    if session is None:
        import requests

        session = requests
    current = url
    for _ in range(max_redirects + 1):
        reason = ssrf_block_reason(current, resolve=True)
        if reason is not None:
            count_blocked(stage, reason)
            raise SSRFBlocked(current, reason)
        response = session.get(current, allow_redirects=False, timeout=timeout, **kwargs)
        location = response.headers.get("Location")
        if response.status_code in REDIRECT_STATUSES and location:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            current = urljoin(str(getattr(response, "url", None) or current), location)
            continue
        return response
    count_blocked(stage, "too_many_redirects")
    raise SSRFBlocked(current, "too_many_redirects")
