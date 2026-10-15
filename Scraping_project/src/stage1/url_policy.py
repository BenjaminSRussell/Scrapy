"""URL allow/block lists: YAML baseline plus a Redis overlay (#633).

``stage1.url_policy`` in config.yml is the shipped baseline. Redis sets
``url_policy:block`` and ``url_policy:allow`` are an overlay that ``cli.py
url-policy`` mutates. Scout re-reads the overlay every
``reload_seconds`` (default 5) so an emergency block applies without a
redeploy. A block rule always wins. An empty allow list (YAML and Redis
both empty) does not restrict; a non-empty allow list drops URLs that
match no allow rule.

Rule shapes (first match wins, ``*`` is the only wildcard):

* glob, when the rule contains ``*`` (matched against the URL, path, and host)
* URL prefix, when the rule contains ``://`` (scheme + exact host + path prefix)
* path prefix, when the rule starts with ``/`` (any host)
* ``host/path`` (that host, including subdomains, plus a path prefix)
* host (that host and its subdomains)

A Redis read failure keeps the last overlay. YAML rules still apply.
``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_PASSWORD`` / ``REDIS_DB`` are the
connection contract; this module does not read ``REDIS_URL``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any, Callable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

BLOCK_KEY = "url_policy:block"
ALLOW_KEY = "url_policy:allow"
AUDIT_KEY = "url_policy:audit"
AUDIT_MAX = 1000
DEFAULT_RELOAD_SECONDS = 5.0

_KINDS = frozenset({"block", "allow"})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _s(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _as_rules(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, bytes):
            item = item.decode("utf-8", "replace")
        if not isinstance(item, str):
            continue
        item = item.strip()
        if item:
            out.append(item)
    return tuple(out)


def _netloc_host(netloc: str) -> str:
    host = (netloc or "").lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    return host.split(":", 1)[0]


def _host_matches(host: str, rule_host: str) -> bool:
    """Exact host or a subdomain. ``example.edu`` matches ``www.example.edu``."""
    host = _netloc_host(host)
    rule = rule_host.lower().strip().strip(".")
    if not host or not rule:
        return False
    return host == rule or host.endswith("." + rule)


def _path_prefix(path: str, rule: str) -> bool:
    """``/calendar`` matches ``/calendar`` and ``/calendar/2024``, not ``/calendar-archive``."""
    path = path or "/"
    rule = rule if rule.startswith("/") else "/" + rule
    if path == rule or path.rstrip("/") == rule.rstrip("/"):
        return True
    base = rule if rule.endswith("/") else rule + "/"
    return path.startswith(base)


def rule_matches(rule: str, url: str) -> bool:
    """True when ``rule`` covers ``url``. See the module docstring for shapes."""
    rule = (rule or "").strip()
    url = (url or "").strip()
    if not rule or not url:
        return False
    if url == rule:
        return True
    parsed = urlparse(url)
    path = parsed.path or "/"
    host = _netloc_host(parsed.netloc)
    if "*" in rule:
        return fnmatch(url, rule) or fnmatch(path, rule) or fnmatch(host, rule.lower())
    if "://" in rule:
        wanted = urlparse(rule)
        if (parsed.scheme or "").lower() != (wanted.scheme or "").lower():
            return False
        if _netloc_host(parsed.netloc) != _netloc_host(wanted.netloc):
            return False
        if wanted.query and (parsed.query or "") != wanted.query:
            return False
        return _path_prefix(path, wanted.path or "/")
    if rule.startswith("/"):
        return _path_prefix(path, rule)
    if "/" in rule:
        rule_host, _, rule_path = rule.partition("/")
        return _host_matches(host, rule_host) and _path_prefix(path, "/" + rule_path)
    return _host_matches(host, rule)


def _first_match(rules: tuple[str, ...] | frozenset[str], url: str) -> str:
    for rule in rules:
        if rule_matches(rule, url):
            return rule
    return ""


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str  # allow | block | not_allowlisted
    rule: str = ""


def decide(
    url: str,
    *,
    yaml_allow: tuple[str, ...],
    yaml_block: tuple[str, ...],
    redis_allow: frozenset[str],
    redis_block: frozenset[str],
) -> PolicyDecision:
    """Block wins. Empty combined allow list means allowlist is off."""
    blocked = _first_match(yaml_block, url) or _first_match(redis_block, url)
    if blocked:
        return PolicyDecision(False, "block", blocked)
    allow_rules = tuple(yaml_allow) + tuple(sorted(redis_allow))
    if not allow_rules:
        return PolicyDecision(True, "allow", "")
    allowed = _first_match(allow_rules, url)
    if allowed:
        return PolicyDecision(True, "allow", allowed)
    return PolicyDecision(False, "not_allowlisted", "")


class UrlPolicy:
    """YAML rules plus a cached Redis overlay and an append-only audit list."""

    def __init__(
        self,
        *,
        yaml_allow: tuple[str, ...] = (),
        yaml_block: tuple[str, ...] = (),
        reload_seconds: float = DEFAULT_RELOAD_SECONDS,
        client: Any = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.yaml_allow = tuple(yaml_allow)
        self.yaml_block = tuple(yaml_block)
        self.reload_seconds = float(reload_seconds)
        self._client = client
        self._clock = clock
        self._redis_allow: frozenset[str] = frozenset()
        self._redis_block: frozenset[str] = frozenset()
        self._loaded_at: float | None = None

    @classmethod
    def from_config(cls, client: Any = None, config: Any = None, clock: Callable[[], float] = time.monotonic) -> UrlPolicy:
        if config is None:
            from src.core.config import get_config

            config = get_config()
        raw_reload = config.get("stage1.url_policy.reload_seconds", DEFAULT_RELOAD_SECONDS)
        try:
            reload_seconds = float(raw_reload)
        except (TypeError, ValueError):
            reload_seconds = DEFAULT_RELOAD_SECONDS
        if reload_seconds < 0:
            reload_seconds = DEFAULT_RELOAD_SECONDS
        return cls(
            yaml_allow=_as_rules(config.get("stage1.url_policy.allow", ())),
            yaml_block=_as_rules(config.get("stage1.url_policy.block", ())),
            reload_seconds=reload_seconds,
            client=client,
            clock=clock,
        )

    @property
    def client(self) -> Any:
        if self._client is None:
            from src.utils.redis import get_redis

            helper = get_redis()
            self._client = getattr(helper, "client", helper)
        return self._client

    def _read_set(self, key: str) -> frozenset[str]:
        raw = self.client.smembers(key)
        if not raw:
            return frozenset()
        if isinstance(raw, (str, bytes)) or not isinstance(raw, (set, list, tuple, frozenset)):
            raise TypeError(f"url policy key {key} returned {type(raw).__name__}, expected a set")
        out: set[str] = set()
        for item in raw:
            if not isinstance(item, (str, bytes)):
                raise TypeError(f"url policy key {key} contained {type(item).__name__}")
            text = _s(item).strip()
            if text:
                out.add(text)
        return frozenset(out)

    def refresh(self, force: bool = False) -> tuple[frozenset[str], frozenset[str]]:
        """Re-read the Redis overlay when the SLA window has elapsed.

        On failure the previous overlay is kept and the exception is re-raised
        so callers that must fail open (scout) can log it. CLI reads should
        surface the error.
        """
        now = self._clock()
        if (
            not force
            and self._loaded_at is not None
            and now - self._loaded_at < self.reload_seconds
        ):
            return self._redis_allow, self._redis_block
        try:
            allow = self._read_set(ALLOW_KEY)
            block = self._read_set(BLOCK_KEY)
        except Exception as exc:
            logger.warning("[URL_POLICY] Redis overlay read failed; keeping the last overlay: %s", exc)
            self._loaded_at = now
            raise
        self._redis_allow = allow
        self._redis_block = block
        self._loaded_at = now
        return allow, block

    def decision(self, url: str) -> PolicyDecision:
        try:
            redis_allow, redis_block = self.refresh()
        except Exception:
            redis_allow, redis_block = self._redis_allow, self._redis_block
        return decide(
            url,
            yaml_allow=self.yaml_allow,
            yaml_block=self.yaml_block,
            redis_allow=redis_allow,
            redis_block=redis_block,
        )

    def allows(self, url: str) -> bool:
        return self.decision(url).allowed

    def _audit(self, action: str, pattern: str, actor: str, reason: str) -> None:
        entry = json.dumps(
            {"action": action, "pattern": pattern, "actor": actor, "reason": reason, "at": _utcnow()}
        )
        pipe = self.client.pipeline()
        pipe.lpush(AUDIT_KEY, entry)
        pipe.ltrim(AUDIT_KEY, 0, AUDIT_MAX - 1)
        pipe.execute()

    def _mutate(self, kind: str, pattern: str, *, actor: str, reason: str, add: bool) -> bool:
        if kind not in _KINDS:
            raise ValueError("kind must be 'block' or 'allow'")
        pattern = (pattern or "").strip()
        if not pattern:
            raise ValueError("pattern is required")
        actor = (actor or "").strip()
        if not actor:
            raise ValueError("actor is required")
        reason = reason or ""
        if add and kind == "block" and not reason.strip():
            raise ValueError("blocking a pattern requires a reason")
        key = BLOCK_KEY if kind == "block" else ALLOW_KEY
        changed = int((self.client.sadd(key, pattern) if add else self.client.srem(key, pattern)) or 0)
        if changed:
            self._audit(f"{kind}_{'add' if add else 'remove'}", pattern, actor, reason)
            self._loaded_at = None
        return bool(changed)

    def add(self, kind: str, pattern: str, *, actor: str, reason: str = "") -> bool:
        """Add a pattern to the Redis overlay. False when it was already present."""
        return self._mutate(kind, pattern, actor=actor, reason=reason, add=True)

    def remove(self, kind: str, pattern: str, *, actor: str, reason: str = "") -> bool:
        """Remove a pattern from the Redis overlay. False when it was absent."""
        return self._mutate(kind, pattern, actor=actor, reason=reason, add=False)

    def audit(self, limit: int = 20) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        rows = self.client.lrange(AUDIT_KEY, 0, max(int(limit), 1) - 1) or []
        for raw in rows:
            try:
                out.append(json.loads(_s(raw)))
            except ValueError:
                out.append({"raw": _s(raw)})
        return out

    def snapshot(self) -> dict[str, Any]:
        redis_allow, redis_block = self.refresh(force=True)
        return {
            "reload_seconds": self.reload_seconds,
            "yaml_allow": list(self.yaml_allow),
            "yaml_block": list(self.yaml_block),
            "redis_allow": sorted(redis_allow),
            "redis_block": sorted(redis_block),
        }
