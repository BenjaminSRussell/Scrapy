"""Outbound TLS verification policy (#584).

Every HTTP client in the pipeline verifies certificates. aiohttp, httpx and
requests do so by default, and nothing here may pass ``verify=False`` /
``ssl=False`` (``tests/unit/test_tls_policy.py`` greps for that).

Scrapy is the exception that needed fixing: its default
``ScrapyClientContextFactory`` accepts ANY certificate, so a MITM could feed
the crawl, and through it the lake, arbitrary content. The project setting now
uses ``BrowserLikeContextFactory``, which verifies the chain and hostname
against the platform trust store.

Exception process: for a host with a broken chain, fix trust (install the CA)
rather than disable verification. As a last resort for a one-off run, set
``SCRAPY_TLS_INSECURE=1``. That is logged at ERROR on startup and exported as
``scrapy_tls_verification_disabled 1`` so it can't go unnoticed in production.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

logger = logging.getLogger(__name__)

INSECURE_ENV = "SCRAPY_TLS_INSECURE"
VERIFYING_FACTORY = "scrapy.core.downloader.contextfactory.BrowserLikeContextFactory"
INSECURE_FACTORY = "scrapy.core.downloader.contextfactory.ScrapyClientContextFactory"

try:
    from prometheus_client import Gauge as _Gauge

    TLS_VERIFICATION_DISABLED = _Gauge(
        "scrapy_tls_verification_disabled",
        "1 when the Scrapy downloader runs with certificate verification disabled (SCRAPY_TLS_INSECURE=1).",
    )
except Exception:  # prometheus_client missing or already registered
    TLS_VERIFICATION_DISABLED = None


def tls_insecure_enabled(env: Mapping[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get(INSECURE_ENV, "") == "1"


def downloader_context_factory(env: Mapping[str, str] | None = None) -> str:
    """Scrapy ``DOWNLOADER_CLIENTCONTEXTFACTORY``: verifying unless explicitly overridden."""
    insecure = tls_insecure_enabled(env)
    if TLS_VERIFICATION_DISABLED is not None:
        TLS_VERIFICATION_DISABLED.set(1 if insecure else 0)
    if insecure:
        logger.error(
            "TLS certificate verification is DISABLED for the Scrapy downloader "
            f"({INSECURE_ENV}=1). Any certificate is accepted; crawled content can be spoofed."
        )
        return INSECURE_FACTORY
    return VERIFYING_FACTORY
