"""#789: expose a queue worker's Prometheus registry over HTTP.

Only the Scrapy process ran a metrics server (``src/scrapy_prometheus.py``,
ports 9410-9420), so every counter registered inside the Stage 2/3/4 workers
(soft bans, deferrals, recency outcomes, SSRF blocks, ...) lived and died in
process memory.  Worker entrypoints call :func:`start_worker_metrics_server`
once at startup.

Environment:
    WORKER_METRICS_ENABLED  "0"/"false"/"no"/"off" disables the server (default on)
    WORKER_METRICS_PORT     listen port (default 9430; outside the Scrapy range)
    WORKER_METRICS_ADDR     bind address (default 0.0.0.0)

A bind failure (port in use, e.g. two workers on one host) is logged and the
worker keeps running: metrics are an observability aid, never a reason to stop
consuming the queue.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_PORT = 9430
_FALSEY = {"0", "false", "no", "off"}

_server: Any = None
_port: int | None = None
_info_gauge: Any = None


# Prometheus scrapes workers from another container, so listen on all
# interfaces by default; WORKER_METRICS_ADDR=127.0.0.1 restricts it.
DEFAULT_METRICS_ADDR = "0.0.0.0"  # nosec B104


def metrics_port(env: Mapping[str, str] | None = None) -> int | None:
    """Port the worker should listen on, or None when disabled."""
    env = os.environ if env is None else env
    if env.get("WORKER_METRICS_ENABLED", "1").strip().lower() in _FALSEY:
        return None
    raw = env.get("WORKER_METRICS_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        logger.warning("WORKER_METRICS_PORT=%r is not an integer; using %d", raw, DEFAULT_PORT)
        return DEFAULT_PORT
    if not 0 <= port <= 65535:
        logger.warning("WORKER_METRICS_PORT=%d out of range; using %d", port, DEFAULT_PORT)
        return DEFAULT_PORT
    return port


def _mark_up(component: str) -> None:
    global _info_gauge
    from prometheus_client import Gauge

    if _info_gauge is None:
        _info_gauge = Gauge(
            "scrapy_worker_up",
            "1 while a pipeline queue worker's metrics endpoint is serving",
            ["component"],
        )
    _info_gauge.labels(component=component).set(1)


def start_worker_metrics_server(component: str, env: Mapping[str, str] | None = None) -> int | None:
    """Start the HTTP exporter once per process; return the bound port or None."""
    global _server, _port
    if _port is not None:
        return _port
    port = metrics_port(env)
    if port is None:
        logger.info("%s worker metrics endpoint disabled (WORKER_METRICS_ENABLED)", component)
        return None
    env = os.environ if env is None else env
    addr = env.get("WORKER_METRICS_ADDR", DEFAULT_METRICS_ADDR).strip() or DEFAULT_METRICS_ADDR
    try:
        from prometheus_client import start_http_server
    except ImportError:  # pragma: no cover - prometheus-client is a hard dependency
        logger.warning("prometheus_client not installed; %s worker metrics not exposed", component)
        return None
    try:
        result = start_http_server(port, addr=addr)
    except OSError as exc:
        logger.warning(
            "%s worker metrics endpoint could not bind %s:%d (%s); metrics not exposed",
            component,
            addr,
            port,
            exc,
        )
        return None
    server = result[0] if isinstance(result, tuple) else None
    bound = server.server_port if server is not None else port
    _server, _port = server, bound
    _mark_up(component)
    logger.info("%s worker metrics on http://%s:%d/metrics", component, addr, bound)
    return bound


def _reset_for_tests() -> None:
    """Stop the server so a test can start a fresh one."""
    global _server, _port
    if _server is not None:
        _server.shutdown()
        _server.server_close()
    _server, _port = None, None
