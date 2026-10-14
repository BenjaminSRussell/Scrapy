"""One Redis connection contract for every process (#511).

Canonical variables (what Compose, Helm and the entrypoints set):

    REDIS_HOST      hostname           (default: localhost)
    REDIS_PORT      port               (default: 6379)
    REDIS_PASSWORD  AUTH password      (optional, #184)
    REDIS_DB        database index     (optional, default 0)

``REDIS_URL`` (``redis://[:password@]host[:port][/db]``) is accepted only as a
fallback for a value that the canonical variables leave unset. When both forms
are set and name different servers, ``REDIS_HOST``/``REDIS_PORT`` win and a
warning is logged once, instead of a silent connection to the wrong server.
kafka-delta-ingest (Rust) applies the same precedence in ``redis_url_from_env``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

logger = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379
_warned: set[tuple[str, int, str, int]] = set()


@dataclass(frozen=True)
class RedisSettings:
    host: str
    port: int
    password: str | None
    db: int

    @property
    def url(self) -> str:
        """``redis://`` URL for clients that only take a URL (password percent-encoded)."""
        auth = f":{quote(self.password, safe='')}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


def _get(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _parse_url(url: str) -> tuple[str | None, int | None, str | None, int | None]:
    parts = urlsplit(url)
    if parts.scheme not in ("redis", "rediss"):
        raise ValueError(f"REDIS_URL must start with redis:// or rediss:// (got {parts.scheme or 'no'} scheme)")
    db = parts.path.lstrip("/")
    return (
        parts.hostname,
        parts.port,
        unquote(parts.password) if parts.password else None,
        int(db) if db else None,
    )


def redis_settings(env: Mapping[str, str] | None = None) -> RedisSettings:
    """Resolve the Redis connection from the environment (see module docstring)."""
    env = os.environ if env is None else env
    host = _get(env, "REDIS_HOST")
    port_raw = _get(env, "REDIS_PORT")
    port = int(port_raw) if port_raw else None
    password = _get(env, "REDIS_PASSWORD")
    db_raw = _get(env, "REDIS_DB")
    db = int(db_raw) if db_raw else None

    url = _get(env, "REDIS_URL")
    if url:
        u_host, u_port, u_password, u_db = _parse_url(url)
        if host and u_host and (host, port or DEFAULT_PORT) != (u_host, u_port or DEFAULT_PORT):
            key = (host, port or DEFAULT_PORT, u_host, u_port or DEFAULT_PORT)
            if key not in _warned:
                _warned.add(key)
                logger.warning(
                    "REDIS_URL (%s:%s) disagrees with REDIS_HOST/REDIS_PORT (%s:%s); using "
                    "REDIS_HOST/REDIS_PORT. Set only REDIS_HOST/REDIS_PORT/REDIS_PASSWORD (#511).",
                    u_host,
                    u_port or DEFAULT_PORT,
                    host,
                    port or DEFAULT_PORT,
                )
        if not host:
            host, port = u_host, port or u_port
        password = password or u_password
        db = db if db is not None else u_db

    return RedisSettings(
        host=host or DEFAULT_HOST,
        port=port or DEFAULT_PORT,
        password=password,
        db=db or 0,
    )
