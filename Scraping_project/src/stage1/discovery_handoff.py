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
    """

    status: DiscoveryStatus | str
    discovered: list[str] = field(default_factory=list)
    error: str | None = None
    checksum: str | None = None
    discovery_source: str = "unknown"
    job_id: str | None = None
    site: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = DiscoveryStatus(self.status)
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
    ) -> "DiscoveryHandoff":
        url_list = list(urls)
        if not url_list:
            return cls.empty(discovery_source=discovery_source, job_id=job_id, site=site)
        return cls(
            status=DiscoveryStatus.SUCCESS,
            discovered=url_list,
            error=None,
            checksum=checksum or checksum_urls(url_list),
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
        )

    @classmethod
    def empty(
        cls,
        *,
        discovery_source: str,
        job_id: str | None = None,
        site: str | None = None,
    ) -> "DiscoveryHandoff":
        return cls(
            status=DiscoveryStatus.EMPTY,
            discovered=[],
            error=None,
            checksum=checksum_urls([]),
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
        )

    @classmethod
    def failed(
        cls,
        error: str,
        *,
        discovery_source: str,
        job_id: str | None = None,
        site: str | None = None,
    ) -> "DiscoveryHandoff":
        return cls(
            status=DiscoveryStatus.FAILED,
            discovered=[],
            error=error,
            checksum=None,
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
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
    """
    if forced_failed:
        return DiscoveryHandoff.failed(
            failure_error or "rustmapper crashed / non-zero exit",
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
        )

    if handoff_path is not None and Path(handoff_path).exists():
        existing = read_handoff_json(handoff_path)
        if existing.is_failed:
            existing.discovery_source = existing.discovery_source or discovery_source
            return existing
        if existing.is_empty:
            return existing

    path = Path(jsonl_path)
    if not path.exists():
        return DiscoveryHandoff.failed(
            f"sitemap.jsonl missing: {path}",
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
        )

    try:
        urls, file_checksum = load_sitemap_jsonl(path)
    except Exception as exc:
        return DiscoveryHandoff.failed(
            f"failed to read sitemap.jsonl: {exc}",
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
        )

    if not urls:
        return DiscoveryHandoff.empty(
            discovery_source=discovery_source,
            job_id=job_id,
            site=site,
        )

    handoff = DiscoveryHandoff.success(
        urls,
        discovery_source=discovery_source,
        job_id=job_id,
        site=site,
        checksum=checksum_urls(urls),
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
