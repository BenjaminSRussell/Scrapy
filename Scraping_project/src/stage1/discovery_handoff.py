"""Discovery handoff artifacts: success | empty | failed (+ JSONL checksum).

Distinguishes "no URLs found" from hard failures so orchestrators do not treat
rustmapper crashes / empty sitemap.jsonl as green incomplete success.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class DiscoveryStatus(str, Enum):
    """Handoff status enum consumed by Scout / SeedManager / orchestrator."""

    SUCCESS = "success"
    EMPTY = "empty"
    FAILED = "failed"


@dataclass
class DiscoveryHandoff:
    """Cross-engine discovery handoff artifact.

    Fields:
        status: success | empty | failed
        discovered: URL list (empty when status is empty/failed)
        error: failure detail (required when failed)
        checksum: sha256 over JSONL body or sorted URL payload
        discovery_source: provenance (sitemap_parser | scout | rustmapper | ...)
        job_id / site: optional coordination metadata
        crawl_delay_seconds: optional host → robots.txt Crawl-Delay seconds
            written by rustmapper after discovery (#939). Canonical JSON is an
            object, ``{"example.edu": 5.0}``. A list of
            ``{"host", "crawl_delay_seconds"}`` objects is accepted on read.
            Hosts are lowercase hostnames (no port, no trailing dot). A missing
            key, a missing host, null, or a non-positive value means that host
            keeps the configured Stage 1 download delay. Scout applies
            ``max(configured, discovered)`` capped at ``ROBOTS_MAX_CRAWL_DELAY``.
    """

    status: DiscoveryStatus | str
    discovered: list[str] = field(default_factory=list)
    error: str | None = None
    checksum: str | None = None
    discovery_source: str = "unknown"
    job_id: str | None = None
    site: str | None = None
    crawl_delay_seconds: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = DiscoveryStatus(self.status)
        self.crawl_delay_seconds = normalize_crawl_delay_map(self.crawl_delay_seconds)
        if self.checksum is None and self.discovered:
            self.checksum = checksum_urls(self.discovered)

    @property
    def is_success(self) -> bool:
        return self.status == DiscoveryStatus.SUCCESS

    @property
    def is_empty(self) -> bool:
        return self.status == DiscoveryStatus.EMPTY

    @property
    def is_failed(self) -> bool:
        return self.status == DiscoveryStatus.FAILED

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value if isinstance(self.status, DiscoveryStatus) else self.status
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryHandoff":
        return cls(
            status=data.get("status", DiscoveryStatus.FAILED),
            discovered=list(data.get("discovered") or []),
            error=data.get("error"),
            checksum=data.get("checksum"),
            discovery_source=data.get("discovery_source", "unknown"),
            job_id=data.get("job_id"),
            site=data.get("site"),
            crawl_delay_seconds=data.get("crawl_delay_seconds") or {},
        )

    @classmethod
    def success(
        cls,
        urls: Iterable[str],
        *,
        discovery_source: str,
        job_id: str | None = None,
        site: str | None = None,
        checksum: str | None = None,
        crawl_delay_seconds: dict[str, float] | None = None,
    ) -> "DiscoveryHandoff":
        url_list = list(urls)
        if not url_list:
            return cls.empty(
                discovery_source=discovery_source,
                job_id=job_id,
                site=site,
                crawl_delay_seconds=crawl_delay_seconds,
            )
        return cls(
            status=DiscoveryStatus.SUCCESS,
            discovered=url_list,
            error=None,
            checksum=checksum or checksum_urls(url_list),
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
            crawl_delay_seconds=crawl_delay_seconds or {},
        )

    @classmethod
    def empty(
        cls,
        *,
        discovery_source: str,
        job_id: str | None = None,
        site: str | None = None,
        crawl_delay_seconds: dict[str, float] | None = None,
    ) -> "DiscoveryHandoff":
        return cls(
            status=DiscoveryStatus.EMPTY,
            discovered=[],
            error=None,
            checksum=checksum_urls([]),
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
            crawl_delay_seconds=crawl_delay_seconds or {},
        )

    @classmethod
    def failed(
        cls,
        error: str,
        *,
        discovery_source: str,
        job_id: str | None = None,
        site: str | None = None,
        crawl_delay_seconds: dict[str, float] | None = None,
    ) -> "DiscoveryHandoff":
        return cls(
            status=DiscoveryStatus.FAILED,
            discovered=[],
            error=error,
            checksum=None,
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
            crawl_delay_seconds=crawl_delay_seconds or {},
        )


def checksum_urls(urls: Iterable[str]) -> str:
    """SHA256 over sorted unique URLs (newline-joined)."""
    payload = "\n".join(sorted(set(urls))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def checksum_jsonl_bytes(raw: bytes) -> str:
    """SHA256 of raw JSONL file bytes (pre-ingest integrity)."""
    return hashlib.sha256(raw).hexdigest()


def checksum_jsonl_file(path: str | Path) -> str:
    """SHA256 of a sitemap.jsonl (or handoff JSONL) file on disk."""
    data = Path(path).read_bytes()
    return checksum_jsonl_bytes(data)


def verify_checksum(urls: Iterable[str], expected: str | None) -> bool:
    """Return True if expected matches checksum_urls(urls). None expected -> False."""
    if not expected:
        return False
    return checksum_urls(urls) == expected


def normalize_host_key(value: Any) -> str | None:
    """Lowercase hostname for a crawl-delay map key. None when it is not a host."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    if "://" in text:
        host = urlparse(text).hostname
    else:
        text = text.split("/")[0]
        if "@" in text:
            text = text.rsplit("@", 1)[-1]
        if text.startswith("[") and "]" in text:
            host = text[1 : text.index("]")]
        elif text.count(":") == 1:
            host = text.split(":", 1)[0]
        else:
            host = text
    if not host:
        return None
    host = host.strip().lower().rstrip(".")
    if not host:
        return None
    try:
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return None
    return host or None


def _coerce_delay_seconds(value: Any) -> float | None:
    """Positive finite seconds, or None (caller keeps the config default)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        value = text
    try:
        delay = float(value)
    except (TypeError, ValueError):
        return None
    if delay != delay or delay in (float("inf"), float("-inf")) or delay <= 0:
        return None
    return delay


def normalize_crawl_delay_map(raw: Any) -> dict[str, float]:
    """Host → Crawl-Delay seconds. Invalid rows are dropped, not fatal.

    Accepts ``{"example.edu": 5}`` or ``[{"host": "example.edu", "crawl_delay_seconds": 5}]``.
    A missing map, a missing host, or a non-positive delay is omitted so Stage 1
    uses the configured download delay for that host. Duplicate hosts keep the
    larger delay.
    """
    items: list[tuple[Any, Any]] = []
    if raw is None:
        return {}
    if isinstance(raw, dict):
        items = list(raw.items())
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            host = entry.get("host") or entry.get("netloc")
            delay = entry.get("crawl_delay_seconds")
            items.append((host, delay))
    else:
        return {}
    out: dict[str, float] = {}
    for host, delay in items:
        key = normalize_host_key(host)
        seconds = _coerce_delay_seconds(delay)
        if key is None or seconds is None:
            continue
        prev = out.get(key)
        if prev is None or seconds > prev:
            out[key] = seconds
    return out


def merge_crawl_delay_maps(*maps: Any) -> dict[str, float]:
    """Union of crawl-delay maps. The same host keeps the larger delay."""
    merged: dict[str, float] = {}
    for raw in maps:
        for host, delay in normalize_crawl_delay_map(raw).items():
            prev = merged.get(host)
            if prev is None or delay > prev:
                merged[host] = delay
    return merged


def crawl_delays_from_jsonl_bytes(raw: bytes) -> dict[str, float]:
    """``crawl_delay_seconds`` from rustmapper sitemap.jsonl objects.

    A line may be a URL record (``url`` / ``loc`` plus the delay) or a host
    record (``host`` plus the delay). Lines without the field are ignored.
    """
    found: dict[str, float] = {}
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or "crawl_delay_seconds" not in obj:
            continue
        host = obj.get("host") or obj.get("netloc") or obj.get("url") or obj.get("loc")
        found = merge_crawl_delay_maps(found, {host: obj.get("crawl_delay_seconds")})
    return found


def load_discovered_crawl_delays(
    *,
    handoff_path: str | Path | None = None,
    jsonl_path: str | Path | None = None,
) -> dict[str, float]:
    """Read ``crawl_delay_seconds`` from a handoff sidecar and/or sitemap JSONL.

    Missing paths and unreadable files yield an empty map (config default).
    """
    delays: dict[str, float] = {}
    if handoff_path:
        path = Path(handoff_path)
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                raw = data.get("crawl_delay_seconds") if isinstance(data, dict) else None
                delays = merge_crawl_delay_maps(delays, raw)
        except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
            logger.warning("discovery handoff crawl_delay_seconds unreadable: %s", path)
    if jsonl_path:
        path = Path(jsonl_path)
        try:
            if path.is_file():
                delays = merge_crawl_delay_maps(delays, crawl_delays_from_jsonl_bytes(path.read_bytes()))
        except (OSError, UnicodeError, TypeError, ValueError):
            logger.warning("sitemap jsonl crawl_delay_seconds unreadable: %s", path)
    return delays


def _delays_for_consume(jsonl_path: str | Path, sidecar_delays: dict[str, float]) -> dict[str, float]:
    """Sidecar delays plus any ``crawl_delay_seconds`` rows in sitemap.jsonl."""
    extra: dict[str, float] = {}
    path = Path(jsonl_path)
    if path.is_file():
        try:
            extra = crawl_delays_from_jsonl_bytes(path.read_bytes())
        except (OSError, UnicodeError, TypeError, ValueError):
            extra = {}
    return merge_crawl_delay_maps(sidecar_delays, extra)


def write_handoff_json(path: str | Path, handoff: DiscoveryHandoff) -> None:
    """Persist handoff artifact as JSON (sidecar for rustmapper / Scout)."""
    Path(path).write_text(json.dumps(handoff.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_handoff_json(path: str | Path) -> DiscoveryHandoff:
    """Load handoff artifact from JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return DiscoveryHandoff.from_dict(data)


def load_sitemap_jsonl(path: str | Path) -> tuple[list[str], str]:
    """Parse rustmapper sitemap.jsonl -> (urls, file_checksum).

    Each line may be a bare URL string or a JSON object with a ``url`` field.
    """
    raw = Path(path).read_bytes()
    file_checksum = checksum_jsonl_bytes(raw)
    urls: list[str] = []
    text = raw.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = obj.get("url") or obj.get("loc")
            if url:
                urls.append(str(url).strip())
        elif line.startswith("http://") or line.startswith("https://"):
            urls.append(line)
    return urls, file_checksum


def consume_rustmapper_handoff(
    jsonl_path: str | Path,
    handoff_path: str | Path | None = None,
    *,
    discovery_source: str = "rustmapper",
    job_id: str | None = None,
    site: str | None = None,
    forced_failed: bool = False,
    failure_error: str | None = None,
) -> DiscoveryHandoff:
    """Build a DiscoveryHandoff from rustmapper outputs.

    - If ``forced_failed`` or handoff JSON says failed -> FAILED (never EMPTY).
    - Empty JSONL with no failure -> EMPTY (needs ack/skip Stage2).
    - Non-empty JSONL -> SUCCESS with file checksum (+ URL payload checksum).
    - ``crawl_delay_seconds`` on the sidecar and on JSONL objects is kept (#939).
    """
    if forced_failed:
        return DiscoveryHandoff.failed(
            failure_error or "rustmapper crashed / non-zero exit",
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
        )

    sidecar_delays: dict[str, float] = {}
    if handoff_path is not None and Path(handoff_path).exists():
        existing = read_handoff_json(handoff_path)
        sidecar_delays = dict(existing.crawl_delay_seconds)
        if existing.is_failed or existing.is_empty:
            existing.discovery_source = existing.discovery_source or discovery_source
            existing.crawl_delay_seconds = _delays_for_consume(jsonl_path, sidecar_delays)
            return existing

    path = Path(jsonl_path)
    if not path.exists():
        return DiscoveryHandoff.failed(
            f"sitemap.jsonl missing: {path}",
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
            crawl_delay_seconds=sidecar_delays,
        )

    try:
        urls, file_checksum = load_sitemap_jsonl(path)
    except Exception as exc:
        return DiscoveryHandoff.failed(
            f"failed to read sitemap.jsonl: {exc}",
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
            crawl_delay_seconds=sidecar_delays,
        )

    delays = _delays_for_consume(path, sidecar_delays)
    if not urls:
        return DiscoveryHandoff.empty(
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
            crawl_delay_seconds=delays,
        )

    handoff = DiscoveryHandoff.success(
        urls,
        discovery_source=discovery_source,
        job_id=job_id,
        site=site,
        checksum=checksum_urls(urls),
        crawl_delay_seconds=delays,
    )
    logger.info(
        "rustmapper handoff success: %d urls payload_checksum=%s file_checksum=%s",
        len(urls),
        handoff.checksum,
        file_checksum,
    )
    return handoff


class DiscoveryHandoffError(RuntimeError):
    """Raised when a failed handoff must abort Stage2 ingest."""


class DiscoveryEmptyNeedsAck(RuntimeError):
    """Raised when empty handoff requires explicit ack/skip before Stage2."""


def require_ingestible(handoff: DiscoveryHandoff, *, ack_empty: bool = False) -> list[str]:
    """Gate SeedManager ingest on handoff status.

    - failed -> abort (raises DiscoveryHandoffError)
    - empty -> needs ack/skip Stage2 (raises unless ack_empty=True)
    - success -> verify checksum then return URLs
    """
    if handoff.is_failed:
        raise DiscoveryHandoffError(
            f"discovery failed ({handoff.discovery_source}): {handoff.error or 'unknown error'}"
        )
    if handoff.is_empty:
        if not ack_empty:
            raise DiscoveryEmptyNeedsAck(
                f"discovery empty ({handoff.discovery_source}); ack/skip Stage2 required"
            )
        return []
    if not verify_checksum(handoff.discovered, handoff.checksum):
        raise DiscoveryHandoffError(
            f"checksum mismatch before SeedManager ingest "
            f"(source={handoff.discovery_source}, expected={handoff.checksum})"
        )
    return list(handoff.discovered)


def ingest_handoff_via_seed_manager(
    seed_manager: Any,
    handoff: DiscoveryHandoff,
    *,
    source_url: str,
    dedup: Any | None = None,
    ack_empty: bool = False,
    write_uconn_urls: bool = True,
    enqueue_stage2: bool = True,
) -> dict[str, Any]:
    """Verify handoff, shared-dedup claim, then SeedManager ingest.

    Returns counts plus handoff status metadata.
    """
    urls = require_ingestible(handoff, ack_empty=ack_empty)
    if not urls:
        return {
            "status": handoff.status.value if isinstance(handoff.status, DiscoveryStatus) else handoff.status,
            "seed_inserted": 0,
            "uconn_inserted": 0,
            "stage2_enqueued": 0,
            "claimed": 0,
            "skipped_dedup": 0,
            "checksum": handoff.checksum,
            "discovery_source": handoff.discovery_source,
        }

    claimed_urls = urls
    skipped = 0
    if dedup is not None:
        claimed_urls, results = dedup.claim_urls(
            urls,
            handoff.discovery_source,
            site=handoff.site or source_url,
        )
        skipped = sum(1 for r in results if not r.claimed)

    if not claimed_urls:
        return {
            "status": handoff.status.value if isinstance(handoff.status, DiscoveryStatus) else handoff.status,
            "seed_inserted": 0,
            "uconn_inserted": 0,
            "stage2_enqueued": 0,
            "claimed": 0,
            "skipped_dedup": skipped,
            "checksum": handoff.checksum,
            "discovery_source": handoff.discovery_source,
        }

    result = seed_manager.add_urls_to_seeds(
        urls=claimed_urls,
        source_url=source_url,
        source_spider=handoff.discovery_source,
        write_uconn_urls=write_uconn_urls,
        enqueue_stage2=enqueue_stage2,
    )
    result = dict(result)
    result.update(
        {
            "status": handoff.status.value if isinstance(handoff.status, DiscoveryStatus) else handoff.status,
            "claimed": len(claimed_urls),
            "skipped_dedup": skipped,
            "checksum": handoff.checksum,
            "discovery_source": handoff.discovery_source,
        }
    )
    return result
