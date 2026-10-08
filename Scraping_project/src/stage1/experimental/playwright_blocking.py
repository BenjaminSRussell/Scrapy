"""Configurable Playwright resource blocking for the JS spider (#390).

The spider used to hard-code ``BLOCKED_RESOURCE_TYPES = [image, stylesheet, font, media]``,
and it installed the block only *after* the page had loaded (to networkidle), so the
initial render fetched everything anyway. Now the policy comes from config:

    stage1:
      js_blocked_resource_types: [image, stylesheet, font, media]
      js_blocked_resource_types_by_domain:
        catalog.example.edu: [image, font]   # this site needs its CSS for content

It is applied from the first request through scrapy-playwright's
``PLAYWRIGHT_ABORT_REQUEST`` hook (:func:`should_abort_request`), and again by the
spider's in-page route while it scrolls. Domain keys match the page's host and its
subdomains, and the most specific key wins. The top-level ``document`` is never blocked.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

DEFAULT_BLOCKED_TYPES: tuple[str, ...] = ("image", "stylesheet", "font", "media")
# Playwright's Request.resource_type values.
RESOURCE_TYPES = frozenset(
    {
        "document", "stylesheet", "image", "media", "font", "script", "texttrack", "xhr",
        "fetch", "eventsource", "websocket", "manifest", "other",
    }
)
NEVER_BLOCK = frozenset({"document"})


def _types(raw: Any, where: str) -> frozenset[str]:
    if raw is None:
        return frozenset()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, Iterable):
        raise ValueError(f"{where}: expected a list of resource types, got {raw!r}")
    out: set[str] = set()
    for item in raw:
        name = str(item).strip().lower()
        if name in NEVER_BLOCK:
            logger.warning(f"[PLAYWRIGHT] {where}: refusing to block '{name}' (the page itself)")
        elif name not in RESOURCE_TYPES:
            raise ValueError(f"{where}: unknown resource type {item!r}; valid: {sorted(RESOURCE_TYPES)}")
        else:
            out.add(name)
    return frozenset(out)


@dataclass(frozen=True)
class BlockPolicy:
    default: frozenset[str] = frozenset(DEFAULT_BLOCKED_TYPES)
    by_domain: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: Any) -> BlockPolicy:
        get = config.get if config is not None else (lambda key, default=None: default)
        default_raw = get("stage1.js_blocked_resource_types", None)
        default = frozenset(DEFAULT_BLOCKED_TYPES) if default_raw is None else _types(
            default_raw, "stage1.js_blocked_resource_types"
        )
        raw_domains = get("stage1.js_blocked_resource_types_by_domain", None) or {}
        if not isinstance(raw_domains, Mapping):
            raise ValueError("stage1.js_blocked_resource_types_by_domain must map domain -> list of types")
        by_domain = {
            str(d).strip().lower().lstrip("."): _types(v, f"stage1.js_blocked_resource_types_by_domain[{d}]")
            for d, v in raw_domains.items()
        }
        return cls(default=default, by_domain=by_domain)

    def types_for(self, page_url: str | None) -> frozenset[str]:
        host = (urlsplit(page_url or "").hostname or "").lower()
        best: str | None = None
        for domain in self.by_domain:
            if host == domain or host.endswith("." + domain):
                if best is None or len(domain) > len(best):
                    best = domain
        return self.by_domain[best] if best is not None else self.default

    def should_block(self, resource_type: str, page_url: str | None) -> bool:
        rtype = (resource_type or "").lower()
        return rtype not in NEVER_BLOCK and rtype in self.types_for(page_url)


_POLICY: BlockPolicy | None = None


def get_policy(refresh: bool = False) -> BlockPolicy:
    global _POLICY
    if _POLICY is None or refresh:
        try:
            from src.core.config import get_config

            config = get_config()
        except Exception as e:  # config unavailable (tests, tooling): fall back to defaults
            logger.debug(f"[PLAYWRIGHT] config unavailable for resource blocking ({e}); using defaults")
            config = None
        _POLICY = BlockPolicy.from_config(config)
    return _POLICY


def _page_url(request: Any) -> str | None:
    try:
        return str(request.frame.page.url)
    except Exception:  # service-worker requests have no frame
        return None


def should_abort_request(request: Any) -> bool:
    """``PLAYWRIGHT_ABORT_REQUEST`` target: abort blocked subresources from the first request."""
    url = _page_url(request)
    if not url or url == "about:blank":
        url = getattr(request, "url", None)
    return get_policy().should_block(getattr(request, "resource_type", ""), url)
