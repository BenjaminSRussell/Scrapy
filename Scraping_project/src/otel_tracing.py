"""OpenTelemetry tracing for Scrapy stages and workers.

Default is off / no-op when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset, or when
the optional ``[otel]`` extra is not installed, so local lean runs keep
working. When the endpoint is set and packages are present, spans export via
OTLP to the collector in ``docker-compose.production.yml`` (gRPC ``4317`` /
HTTP ``4318``).

Install optional deps::

    pip install -e ".[otel]"

Span attributes (when available):
  - scrapy.stage
  - scrapy.spider
  - scrapy.crawl_job_id
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_CRAWL_JOB_ID: ContextVar[str | None] = ContextVar("crawl_job_id", default=None)
_SPIDER_NAME: ContextVar[str | None] = ContextVar("spider_name", default=None)

_tracer: Any = None
_initialized = False
_enabled = False

# Soft-import: missing optional [otel] packages must not break lean installs.
try:
    from opentelemetry import trace as _otel_trace
    from opentelemetry.sdk.resources import Resource as _OtelResource
    from opentelemetry.sdk.trace import TracerProvider as _OtelTracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor as _OtelBatchSpanProcessor

    _OTEL_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - lean installs without [otel]
    _otel_trace = None  # type: ignore[assignment]
    _OtelResource = None  # type: ignore[assignment]
    _OtelTracerProvider = None  # type: ignore[assignment]
    _OtelBatchSpanProcessor = None  # type: ignore[assignment]
    _OTEL_SDK_AVAILABLE = False


def is_otel_enabled() -> bool:
    return bool(os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip())


def get_crawl_job_id() -> str | None:
    return _CRAWL_JOB_ID.get() or os.getenv("CRAWL_JOB_ID") or None


def set_crawl_job_id(crawl_job_id: str) -> None:
    _CRAWL_JOB_ID.set(crawl_job_id)


def get_spider_name() -> str | None:
    return _SPIDER_NAME.get()


def set_spider_name(name: str) -> None:
    _SPIDER_NAME.set(name)


def ensure_crawl_job_id() -> str:
    existing = get_crawl_job_id()
    if existing:
        return existing
    new_id = str(uuid.uuid4())
    set_crawl_job_id(new_id)
    return new_id


def _insecure_endpoint(endpoint: str) -> bool:
    return endpoint.startswith("http://") or "://" not in endpoint


def _http_traces_endpoint(endpoint: str) -> str:
    if endpoint.rstrip("/").endswith("/v1/traces"):
        return endpoint
    return endpoint.rstrip("/") + "/v1/traces"


def reset_tracing_state_for_tests() -> None:
    """Reset module globals (unit tests only)."""
    global _tracer, _initialized, _enabled
    _tracer = None
    _initialized = False
    _enabled = False
    _CRAWL_JOB_ID.set(None)
    _SPIDER_NAME.set(None)


def init_tracing(service_name: str | None = None) -> bool:
    """Initialize the OTEL SDK when an exporter endpoint is configured.

    Returns False (no-op) when the endpoint is unset, optional packages are
    missing, or initialization fails — never raises into the crawl path.
    """
    global _tracer, _initialized, _enabled

    if _initialized:
        return _enabled

    _initialized = True
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.debug("OTEL tracing disabled (OTEL_EXPORTER_OTLP_ENDPOINT unset)")
        _enabled = False
        return False

    if not _OTEL_SDK_AVAILABLE:
        logger.debug(
            "OTEL tracing disabled (optional packages missing). "
            "Install with: pip install -e '.[otel]'"
        )
        _enabled = False
        return False

    try:
        assert _otel_trace is not None
        assert _OtelResource is not None
        assert _OtelTracerProvider is not None
        assert _OtelBatchSpanProcessor is not None

        service = service_name or os.getenv("OTEL_SERVICE_NAME", "scrapy-pipeline")
        resource = _OtelResource.create(
            {
                "service.name": service,
                "service.namespace": "scraping",
            }
        )
        provider = _OtelTracerProvider(resource=resource)

        protocol = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower().strip()
        if protocol in ("http/protobuf", "http"):
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=_http_traces_endpoint(endpoint))
        else:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(
                endpoint=endpoint,
                insecure=_insecure_endpoint(endpoint),
            )

        provider.add_span_processor(_OtelBatchSpanProcessor(exporter))
        _otel_trace.set_tracer_provider(provider)
        _tracer = _otel_trace.get_tracer("scrapy.otel", "1.0.0")
        _enabled = True
        logger.info("OTEL tracing enabled → %s (protocol=%s)", endpoint, protocol)
        return True
    except ImportError as exc:
        logger.warning(
            "OTEL exporter import failed (%s) — continuing as no-op. "
            "Install with: pip install -e '.[otel]'",
            exc,
        )
        _enabled = False
        return False
    except Exception as exc:  # noqa: BLE001 — never break crawls on tracer setup
        logger.warning("Failed to initialize OTEL tracing: %s — continuing as no-op", exc)
        _enabled = False
        return False


def get_tracer() -> Any:
    if not _initialized:
        init_tracing()
    return _tracer


def _base_attributes(
    stage: str | None = None,
    spider: str | None = None,
    crawl_job_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    spider_val = spider or get_spider_name()
    job_val = crawl_job_id or get_crawl_job_id()
    if stage:
        attrs["scrapy.stage"] = stage
    if spider_val:
        attrs["scrapy.spider"] = spider_val
    if job_val:
        attrs["scrapy.crawl_job_id"] = job_val
    if extra:
        for key, value in extra.items():
            if value is not None:
                attrs[key] = value
    return attrs


@contextmanager
def start_span(
    name: str,
    *,
    stage: str | None = None,
    spider: str | None = None,
    crawl_job_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Start a span, or yield ``None`` when tracing is disabled / unavailable."""
    tracer = get_tracer()
    attrs = _base_attributes(stage, spider, crawl_job_id, attributes)

    if not _enabled or tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name, attributes=attrs) as span:
        yield span


def traced_pipeline_process(stage_name: str):
    """Decorator for Scrapy ``process_item`` methods."""

    def decorator(func):
        def wrapper(self, item, spider):
            with start_span(
                f"pipeline.{stage_name}",
                stage=stage_name,
                spider=getattr(spider, "name", None),
                crawl_job_id=getattr(spider, "crawl_job_id", None) or get_crawl_job_id(),
            ):
                return func(self, item, spider)

        return wrapper

    return decorator


class OtelItemPipeline:
    """Propagate crawl-job ID onto items and emit a short pipeline span.

    Registered early so downstream stages / Kafka / Delta writers can carry
    the same correlation id. No-ops when the collector endpoint is unset or
    optional OTEL packages are missing.
    """

    @classmethod
    def from_crawler(cls, crawler):
        init_tracing(
            service_name=crawler.settings.get("OTEL_SERVICE_NAME", "scrapy-pipeline")
        )
        return cls()

    def process_item(self, item, spider):
        crawl_job_id = getattr(spider, "crawl_job_id", None) or ensure_crawl_job_id()
        try:
            from itemadapter import ItemAdapter

            adapter = ItemAdapter(item)
            if "crawl_job_id" not in adapter or not adapter.get("crawl_job_id"):
                adapter["crawl_job_id"] = crawl_job_id
        except Exception:  # noqa: BLE001 — never drop items for tracing
            pass

        with start_span(
            "pipeline.item",
            stage="item_pipeline",
            spider=getattr(spider, "name", None),
            crawl_job_id=crawl_job_id,
        ):
            return item


class OtelTracingExtension:
    """Scrapy extension: root crawl span + crawl-job ID on the spider."""

    def __init__(self) -> None:
        self._crawl_spans: dict[str, Any] = {}
        self._context_tokens: dict[str, Any] = {}

    @classmethod
    def from_crawler(cls, crawler):
        from scrapy import signals
        from scrapy.exceptions import NotConfigured

        if not crawler.settings.getbool("OTEL_ENABLED", True):
            raise NotConfigured("OTEL tracing extension is disabled")

        init_tracing(
            service_name=crawler.settings.get("OTEL_SERVICE_NAME", "scrapy-pipeline")
        )

        ext = cls()
        crawler.signals.connect(ext.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(ext.spider_closed, signal=signals.spider_closed)
        return ext

    def spider_opened(self, spider) -> None:
        crawl_job_id = (
            getattr(spider, "crawl_job_id", None)
            or os.getenv("CRAWL_JOB_ID")
            or str(uuid.uuid4())
        )
        spider.crawl_job_id = crawl_job_id
        set_crawl_job_id(crawl_job_id)
        set_spider_name(spider.name)

        if not _enabled:
            logger.debug(
                "OTEL no-op for spider %s (crawl_job_id=%s)",
                spider.name,
                crawl_job_id,
            )
            return

        tracer = get_tracer()
        if tracer is None:
            return

        try:
            from opentelemetry import context, trace
        except ImportError:
            logger.debug("OTEL packages unavailable during spider_opened — no-op")
            return

        span = tracer.start_span(
            f"crawl.{spider.name}",
            attributes=_base_attributes(
                stage="stage1",
                spider=spider.name,
                crawl_job_id=crawl_job_id,
                extra={"scrapy.signal": "spider_opened"},
            ),
        )
        token = context.attach(trace.set_span_in_context(span))
        self._crawl_spans[spider.name] = span
        self._context_tokens[spider.name] = token
        logger.info(
            "OTEL crawl span started for %s crawl_job_id=%s",
            spider.name,
            crawl_job_id,
        )

    def spider_closed(self, spider, reason: str) -> None:
        span = self._crawl_spans.pop(spider.name, None)
        token = self._context_tokens.pop(spider.name, None)

        if span is not None:
            try:
                span.set_attribute("scrapy.close_reason", str(reason))
            except Exception:  # noqa: BLE001
                pass
            try:
                span.end()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error ending crawl span: %s", exc)

        if token is not None:
            try:
                from opentelemetry import context

                context.detach(token)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error detaching OTEL context: %s", exc)

        if _enabled:
            try:
                from opentelemetry import trace

                provider = trace.get_tracer_provider()
                force_flush = getattr(provider, "force_flush", None)
                if callable(force_flush):
                    force_flush(timeout_millis=5000)
            except Exception as exc:  # noqa: BLE001
                logger.debug("OTEL force_flush failed: %s", exc)

        logger.info(
            "OTEL crawl span closed for %s reason=%s crawl_job_id=%s",
            spider.name,
            reason,
            getattr(spider, "crawl_job_id", get_crawl_job_id()),
        )
