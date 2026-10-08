"""SSRF guard for server-side fetches (#450).

Stage 2 and the ASR media downloader fetch URLs taken from crawled pages, so a
hostile page can point them at ``http://169.254.169.254/`` (cloud metadata),
``http://redis:6379`` or any RFC 1918 service. This module blocks every
non-public destination:

* only ``http``/``https`` URLs with a host;
* hostnames ``localhost``, ``*.localhost``, ``*.internal`` and the
  well-known metadata names;
* any IP (literal or DNS-resolved) that is not globally routable: loopback,
  RFC 1918, link-local (incl. 169.254.169.254), CGNAT, unique-local,
  multicast, reserved, unspecified, plus IPv4-mapped IPv6 forms of those.

Checks run before the request and again at connect time (``SafeResolver``
for aiohttp; per-hop checks in ``safe_get`` for requests), so redirects and DNS
answers that change between check and connect are covered too.

``FETCH_ALLOWED_CIDRS`` (comma-separated CIDRs, e.g. ``10.20.0.0/16``) opts
specific internal ranges back in, for campus sites on private addresses or
local test servers.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})
BLOCKED_HOSTNAMES = frozenset({"metadata", "metadata.google.internal", "instance-data", "instance-data.ec2.internal"})
BLOCKED_SUFFIXES = (".internal",)
MAX_REDIRECTS = 5


class UnsafeURLError(OSError):
    """The URL points at a non-public destination. An OSError so aiohttp's
    connector keeps it as ``os_error`` when raised from the resolver."""


def _allowed_networks() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    nets = []
    for raw in os.environ.get("FETCH_ALLOWED_CIDRS", "").split(","):
        raw = raw.strip()
        if raw:
            try:
                nets.append(ipaddress.ip_network(raw, strict=False))
            except ValueError:
                continue
    return nets


def is_blocked_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0].strip("[]"))
    except ValueError:
        return True  # not an IP we can reason about
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if any(ip.version == net.version and ip in net for net in _allowed_networks()):
        return False
    return not ip.is_global or ip.is_multicast


def _host_of(url: str) -> str:
    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise UnsafeURLError(f"unparsable URL: {e}") from e
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(f"scheme {parsed.scheme!r} is not allowed")
    try:
        host = (parsed.hostname or "").rstrip(".").lower()
    except ValueError as e:
        raise UnsafeURLError(f"bad host: {e}") from e
    if not host:
        raise UnsafeURLError("URL has no host")
    if host in BLOCKED_HOSTNAMES or host.endswith(BLOCKED_SUFFIXES):
        # "localhost" is not listed: it resolves to loopback, which the address
        # check blocks unless FETCH_ALLOWED_CIDRS opts loopback in (tests).
        raise UnsafeURLError(f"host {host!r} is internal")
    return host


def _literal_ip(host: str) -> str | None:
    """The address an IP-literal host means, including the integer/hex/short
    IPv4 forms (``2130706433``, ``0x7f.1``, ``127.1``) that resolvers accept."""
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        pass
    if re.fullmatch(r"(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}", host):
        try:
            return socket.inet_ntoa(socket.inet_aton(host))
        except OSError:
            return None
    return None


def _check_addresses(host: str, addresses: list[str]) -> None:
    blocked = [a for a in addresses if is_blocked_ip(a)]
    if blocked:
        raise UnsafeURLError(f"{host} resolves to non-public address {blocked[0]}")


def check_url(url: str, resolve: bool = True) -> None:
    """Raise UnsafeURLError unless ``url`` is an http(s) URL to a public host."""
    host = _host_of(url)
    literal = _literal_ip(host)
    if literal is not None:
        _check_addresses(host, [literal])
        return
    if not resolve:
        return
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return  # unresolvable: the fetch itself will fail; nothing to reach
    _check_addresses(host, [str(info[4][0]) for info in infos])


async def check_url_async(url: str) -> None:
    host = _host_of(url)
    literal = _literal_ip(host)
    if literal is not None:
        _check_addresses(host, [literal])
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError):
        return
    _check_addresses(host, [str(info[4][0]) for info in infos])


def safe_get(url: str, *, session: Any = None, max_redirects: int = MAX_REDIRECTS, **kwargs: Any) -> Any:
    """``requests.get`` that checks the URL and every redirect hop."""
    import requests

    getter = session.get if session is not None else requests.get
    kwargs["allow_redirects"] = False
    current = url
    for _ in range(max_redirects + 1):
        check_url(current)
        response = getter(current, **kwargs)
        location = response.headers.get("Location") if response.is_redirect else None
        if not location:
            return response
        response.close()
        current = urljoin(current, location)
    raise UnsafeURLError(f"too many redirects from {url}")


def safe_resolver() -> Any:
    """aiohttp resolver that refuses non-public answers at connect time."""
    from aiohttp.abc import AbstractResolver
    from aiohttp.resolver import DefaultResolver

    class SafeResolver(AbstractResolver):
        def __init__(self) -> None:
            self._inner = DefaultResolver()

        async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET) -> Any:
            results = await self._inner.resolve(host, port, family)
            _check_addresses(host, [r["host"] for r in results])
            return results

        async def close(self) -> None:
            await self._inner.close()

    return SafeResolver()


def redirect_guard() -> Any:
    """aiohttp TraceConfig that refuses redirects to unsafe IP literals/hosts.

    Hostname targets are additionally re-checked by ``safe_resolver`` when the
    connector resolves them; IP literals skip the resolver, hence this hook.
    """
    import aiohttp

    async def on_redirect(session: Any, ctx: Any, params: Any) -> None:
        location = params.response.headers.get("Location")
        if location:
            target = urljoin(str(params.url), location)
            host = _host_of(target)
            literal = _literal_ip(host)
            if literal is not None:
                _check_addresses(host, [literal])

    trace = aiohttp.TraceConfig()
    trace.on_request_redirect.append(on_redirect)
    return trace
