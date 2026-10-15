"""Stage 1 handoff for a rustmapper ``--data-dir`` resume (#937).

rustmapper already resumes a crawl from ``data-dir``. This module is the
Stage 1 side of that handoff: validate the on-disk artifacts, inject only
URLs that are new since the Stage 1 checkpoint, and refuse a corrupt
directory instead of reseeding.

The layout (and what Stage 1 does with each file) is documented in
``Scraping_project/docs/rustmapper_resume.md``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.stage1.discovery_handoff import DiscoveryHandoff, ingest_handoff_via_seed_manager

logger = logging.getLogger(__name__)

CHECKPOINT_NAME = "stage1_resume.json"
SEEN_NAME = "stage1_resume.seen"
INCREMENTAL_NAME = "crawl_results_incremental.jsonl"
SNAPSHOT_NAME = "sitemap.jsonl"
RESUME_SOURCE_URL = "https://rustmapper.invalid/resume"
_MAX_LINE_BYTES = 1024 * 1024
_CHECKPOINT_VERSION = 1

# CRC-32C (Castagnoli), the polynomial rustmapper's crc32fast WAL uses.
_CRC32C_TABLE = []
for _i in range(256):
    _crc = _i
    for _ in range(8):
        if _crc & 1:
            _crc = (_crc >> 1) ^ 0x82F63B78
        else:
            _crc >>= 1
    _CRC32C_TABLE.append(_crc)
del _i, _crc


class RustmapperResumeError(RuntimeError):
    """Data-dir is missing or corrupt. Callers must not reseed."""


@dataclass
class ResumePlan:
    """URLs to inject and the checkpoint to persist after a successful inject."""

    urls: list[str]
    checkpoint: dict[str, Any]
    new_seen: list[str]
    dirty: bool


def crc32c(data: bytes) -> int:
    """CRC-32C of ``data`` (init/xorout 0xFFFFFFFF), matching crc32fast."""
    crc = 0xFFFFFFFF
    table = _CRC32C_TABLE
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def encode_wal_record(instance_id: int, local_seqno: int, payload: bytes) -> bytes:
    """On-disk rustmapper WAL frame: ``[u32 le len][u32 le crc32c][seqno][payload]``.

    ``seqno`` is ``instance_id`` then ``local_seqno``, both little-endian u64.
    The CRC covers the seqno bytes and the payload, not the length prefix.
    """
    seq = int(instance_id).to_bytes(8, "little") + int(local_seqno).to_bytes(8, "little")
    crc = crc32c(seq + payload)
    return len(payload).to_bytes(4, "little") + crc.to_bytes(4, "little") + seq + payload


def plan_resume(data_dir: str | Path) -> ResumePlan:
    """Validate ``data_dir`` and return only URLs new since the Stage 1 checkpoint.

    Does not write the checkpoint and does not touch SeedManager. An unchanged
    incremental prefix and an unchanged ``sitemap.jsonl`` checksum are not
    JSON-parsed. Raises ``RustmapperResumeError`` when the directory is
    unusable; callers must not fall back to a full reseed.
    """
    root = Path(data_dir)
    if not root.is_dir():
        raise RustmapperResumeError(f"rustmapper data-dir is not a directory: {root}")
    if not _has_rustmapper_artifact(root):
        raise RustmapperResumeError(f"rustmapper data-dir has no resume artifacts: {root}")

    _validate_wal(root)
    prior = _read_checkpoint(root)
    seen = _read_seen(root)
    ckpt = dict(prior) if prior is not None else _blank_checkpoint()
    had_prior = prior is not None

    urls: list[str] = []
    incremental_changed = False
    incremental = root / INCREMENTAL_NAME
    if incremental.is_file():
        inc_urls, inc_updates, incremental_changed = _incremental_delta(
            incremental, ckpt, seen, had_prior
        )
        ckpt.update(inc_updates)
        urls.extend(inc_urls)

    snapshot = root / SNAPSHOT_NAME
    if snapshot.is_file():
        snap_urls, snap_updates = _snapshot_delta(
            snapshot, prior, seen, incremental_changed
        )
        ckpt.update(snap_updates)
        urls.extend(snap_urls)

    filtered, new_seen = _without_seen(_dedupe(urls), seen)
    previous_count = int(ckpt.get("urls_injected") or 0)
    ckpt["urls_injected"] = previous_count + len(filtered)
    ckpt["version"] = _CHECKPOINT_VERSION
    dirty = bool(new_seen) or _cursor_changed(prior, ckpt)
    if not filtered and not _cursor_changed(prior, ckpt):
        ckpt["urls_injected"] = previous_count
        dirty = False
    return ResumePlan(urls=filtered, checkpoint=ckpt, new_seen=new_seen, dirty=dirty)


def commit_resume(data_dir: str | Path, plan: ResumePlan) -> None:
    """Persist the checkpoint, then the seen-set. No-op when ``plan`` is clean."""
    if not plan.dirty and not plan.new_seen:
        return
    root = Path(data_dir)
    _atomic_write(root / CHECKPOINT_NAME, _checkpoint_bytes(plan.checkpoint))
    if plan.new_seen:
        seen_path = root / SEEN_NAME
        existing = seen_path.read_bytes() if seen_path.is_file() else b""
        if existing and not existing.endswith(b"\n"):
            existing += b"\n"
        _atomic_write(seen_path, existing + ("\n".join(plan.new_seen) + "\n").encode("ascii"))


def inject_resume(
    data_dir: str | Path,
    seed_manager: Any | None = None,
    *,
    dedup: Any | None = None,
    source_url: str = RESUME_SOURCE_URL,
    job_id: str | None = None,
    site: str | None = None,
    ack_empty: bool = True,
    write_uconn_urls: bool = True,
    enqueue_stage2: bool = True,
) -> dict[str, Any]:
    """Inject URLs from ``plan_resume`` and advance the checkpoint only if they land.

    A clean directory with nothing new returns ``injected=0`` and does not
    call SeedManager. A throttle or a failed write leaves the checkpoint
    where it was so the next resume retries the same URLs. Corrupt artifacts
    raise ``RustmapperResumeError`` before any insert.
    """
    plan = plan_resume(data_dir)
    if not plan.urls:
        if plan.dirty:
            commit_resume(data_dir, plan)
        return {
            "status": "empty",
            "injected": 0,
            "claimed": 0,
            "seed_inserted": 0,
            "stage2_enqueued": 0,
            "discovery_source": "rustmapper",
        }

    if seed_manager is None:
        raise RustmapperResumeError("seed manager is required to inject resume URLs")
    handoff = DiscoveryHandoff.success(
        plan.urls,
        discovery_source="rustmapper",
        job_id=job_id,
        site=site,
    )
    result = dict(
        ingest_handoff_via_seed_manager(
            seed_manager,
            handoff,
            source_url=source_url,
            dedup=dedup,
            ack_empty=ack_empty,
            write_uconn_urls=write_uconn_urls,
            enqueue_stage2=enqueue_stage2,
        )
    )
    claimed = _as_count(result.get("claimed"))
    inserted = _as_count(result.get("seed_inserted"))
    enqueued = _as_count(result.get("stage2_enqueued"))
    if claimed > 0 and inserted == 0 and enqueued == 0:
        logger.warning(
            "rustmapper resume left the checkpoint unchanged; %s URL(s) did not land in seeds or stage2",
            claimed,
        )
        result["injected"] = 0
        result["status"] = "retry"
        return result

    commit_resume(data_dir, plan)
    result["injected"] = claimed
    result["status"] = result.get("status") or "success"
    logger.info(
        "rustmapper resume injected %s URL(s) from %s",
        result["injected"],
        data_dir,
    )
    return result


def _as_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _blank_checkpoint() -> dict[str, Any]:
    return {
        "version": _CHECKPOINT_VERSION,
        "incremental_offset": 0,
        "incremental_prefix_sha256": None,
        "incremental_size": None,
        "snapshot_sha256": None,
        "snapshot_size": None,
        "urls_injected": 0,
    }


def _has_rustmapper_artifact(root: Path) -> bool:
    names = (
        "crawler_state.redb",
        "wal.log",
        "wal.offset",
        INCREMENTAL_NAME,
        SNAPSHOT_NAME,
        "sitemap.xml",
    )
    return any((root / name).exists() for name in names)


def _cursor_changed(prior: dict[str, Any] | None, ckpt: dict[str, Any]) -> bool:
    if prior is None:
        keys = (
            "incremental_offset",
            "incremental_prefix_sha256",
            "incremental_size",
            "snapshot_sha256",
            "snapshot_size",
        )
        blank = _blank_checkpoint()
        return any(ckpt.get(key) != blank[key] for key in keys)
    keys = (
        "incremental_offset",
        "incremental_prefix_sha256",
        "incremental_size",
        "snapshot_sha256",
        "snapshot_size",
        "urls_injected",
    )
    return any(prior.get(key) != ckpt.get(key) for key in keys)


def _checkpoint_bytes(ckpt: dict[str, Any]) -> bytes:
    return (json.dumps(ckpt, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _read_checkpoint(root: Path) -> dict[str, Any] | None:
    path = root / CHECKPOINT_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RustmapperResumeError(f"corrupt {CHECKPOINT_NAME}: {exc}") from exc
    if not isinstance(data, dict):
        raise RustmapperResumeError(f"corrupt {CHECKPOINT_NAME}: not an object")
    if data.get("version") != _CHECKPOINT_VERSION:
        raise RustmapperResumeError(f"corrupt {CHECKPOINT_NAME}: unsupported version")
    offset = _require_int(data.get("incremental_offset", 0), "incremental_offset")
    injected = _require_int(data.get("urls_injected", 0), "urls_injected")
    prefix = _require_hash(data.get("incremental_prefix_sha256"), "incremental_prefix_sha256")
    snapshot = _require_hash(data.get("snapshot_sha256"), "snapshot_sha256")
    size = data.get("incremental_size")
    snap_size = data.get("snapshot_size")
    if size is not None:
        size = _require_int(size, "incremental_size")
    if snap_size is not None:
        snap_size = _require_int(snap_size, "snapshot_size")
    if offset > 0 and prefix is None:
        raise RustmapperResumeError(f"corrupt {CHECKPOINT_NAME}: offset without prefix hash")
    return {
        "version": _CHECKPOINT_VERSION,
        "incremental_offset": offset,
        "incremental_prefix_sha256": prefix,
        "incremental_size": size,
        "snapshot_sha256": snapshot,
        "snapshot_size": snap_size,
        "urls_injected": injected,
    }


def _require_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RustmapperResumeError(f"corrupt {CHECKPOINT_NAME}: {name}")
    return value


def _require_hash(value: object, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise RustmapperResumeError(f"corrupt {CHECKPOINT_NAME}: {name}")
    return value


def _read_seen(root: Path) -> set[str]:
    path = root / SEEN_NAME
    if not path.exists():
        return set()
    try:
        text = path.read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise RustmapperResumeError(f"corrupt {SEEN_NAME}: {exc}") from exc
    seen: set[str] = set()
    for line in text.splitlines():
        item = line.strip()
        if not item:
            continue
        if len(item) != 64 or any(c not in "0123456789abcdef" for c in item):
            raise RustmapperResumeError(f"corrupt {SEEN_NAME}")
        seen.add(item)
    return seen


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _dedupe(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _without_seen(urls: list[str], seen: set[str]) -> tuple[list[str], list[str]]:
    kept: list[str] = []
    hashes: list[str] = []
    local = set(seen)
    for url in urls:
        digest = _url_hash(url)
        if digest in local:
            continue
        local.add(digest)
        kept.append(url)
        hashes.append(digest)
    return kept, hashes


def _sha256_prefix(path: Path, nbytes: int) -> str:
    digest = hashlib.sha256()
    remaining = nbytes
    with path.open("rb") as handle:
        while remaining:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                raise RustmapperResumeError(f"short read hashing {path.name}")
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_from(path: Path, offset: int) -> tuple[list[str], int]:
    """Parse complete JSONL lines at ``offset``. A torn tail is not consumed."""
    with path.open("rb") as handle:
        handle.seek(offset)
        data = handle.read()
    if not data:
        return [], offset
    newline = data.rfind(b"\n")
    if newline == -1:
        return [], offset
    body = data[: newline + 1]
    urls: list[str] = []
    for raw in body.splitlines():
        if not raw.strip():
            continue
        if len(raw) > _MAX_LINE_BYTES:
            raise RustmapperResumeError(f"corrupt {path.name}: line exceeds {_MAX_LINE_BYTES} bytes")
        urls.append(_url_from_line(raw, name=path.name))
    return urls, offset + len(body)


def _url_from_line(line: bytes, *, name: str) -> str:
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RustmapperResumeError(f"corrupt {name}: line is not utf-8") from exc
    stripped = text.strip()
    if stripped.startswith(("http://", "https://")) and not stripped.startswith("{"):
        return stripped
    if not stripped.startswith("{"):
        raise RustmapperResumeError(f"corrupt {name}: unrecognized line")
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise RustmapperResumeError(f"corrupt {name}: invalid JSON") from exc
    if not isinstance(obj, dict):
        raise RustmapperResumeError(f"corrupt {name}: JSON line is not an object")
    for key in ("url", "url_normalized", "loc"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise RustmapperResumeError(f"corrupt {name}: object has no url")


def _incremental_delta(
    path: Path,
    ckpt: dict[str, Any],
    seen: set[str],
    had_prior: bool,
) -> tuple[list[str], dict[str, Any], bool]:
    """Return new incremental URLs, cursor fields, and whether the file changed.

    The third flag is true only when a previous checkpoint exists and this
    file is a new batch. That lets the snapshot path skip a full re-parse.
    """
    size = path.stat().st_size
    offset = int(ckpt.get("incremental_offset") or 0)
    prefix = ckpt.get("incremental_prefix_sha256")

    def consume_all() -> tuple[list[str], dict[str, Any], bool]:
        urls, end = _parse_from(path, 0)
        updates = {
            "incremental_offset": end,
            "incremental_prefix_sha256": _sha256_prefix(path, end),
            "incremental_size": size,
        }
        return urls, updates, had_prior

    if not prefix:
        return consume_all()

    if offset > size or _sha256_prefix(path, offset) != prefix:
        if offset > 0 and not seen:
            raise RustmapperResumeError(
                f"{INCREMENTAL_NAME} was replaced and the Stage 1 seen-set is missing"
            )
        urls, updates, _changed = consume_all()
        return urls, updates, True

    if size == offset:
        return [], {"incremental_size": size}, False

    urls, end = _parse_from(path, offset)
    updates = {
        "incremental_offset": end,
        "incremental_prefix_sha256": _sha256_prefix(path, end),
        "incremental_size": size,
    }
    return urls, updates, end != offset


def _snapshot_delta(
    path: Path,
    prior: dict[str, Any] | None,
    seen: set[str],
    incremental_changed: bool,
) -> tuple[list[str], dict[str, Any]]:
    digest = _sha256_file(path)
    size = path.stat().st_size
    updates = {"snapshot_sha256": digest, "snapshot_size": size}
    prev = None if prior is None else prior.get("snapshot_sha256")
    if prev == digest:
        return [], updates
    # A later resume already has the previous snapshot in the seen-set.
    # When the incremental file moved, it is the delta; do not re-parse the
    # rewritten full snapshot.
    if incremental_changed and prev:
        return [], updates
    urls, _end = _parse_from(path, 0)
    return urls, updates


def _validate_wal(root: Path) -> None:
    offset_path = root / "wal.offset"
    log_path = root / "wal.log"
    if not offset_path.exists() and not log_path.exists():
        return
    checkpoint: int | None = None
    if offset_path.exists():
        raw = offset_path.read_bytes()
        if len(raw) != 8:
            raise RustmapperResumeError("wal.offset must be exactly 8 little-endian bytes")
        checkpoint = int.from_bytes(raw, "little")
        if not log_path.is_file():
            raise RustmapperResumeError("wal.offset present without wal.log")
    data = log_path.read_bytes() if log_path.is_file() else b""
    if checkpoint is not None and checkpoint > len(data):
        raise RustmapperResumeError(
            f"wal.offset {checkpoint} is past wal.log length {len(data)}"
        )
    if checkpoint is None:
        _replay_wal(data, allow_torn=True)
        return
    consumed = _replay_wal(data[:checkpoint], allow_torn=False)
    if consumed != checkpoint:
        raise RustmapperResumeError("wal.offset is not on a WAL record boundary")
    _replay_wal(data[checkpoint:], allow_torn=True)


def _replay_wal(data: bytes, *, allow_torn: bool) -> int:
    """Walk WAL frames. A torn tail is ignored only when ``allow_torn`` is set.

    A CRC mismatch is always fatal. Returns the number of bytes consumed,
    which excludes an ignored torn tail.
    """
    pos = 0
    limit = len(data)
    while pos < limit:
        if limit - pos < 4:
            if allow_torn:
                return pos
            raise RustmapperResumeError("torn WAL record inside the checkpoint")
        payload_len = int.from_bytes(data[pos : pos + 4], "little")
        frame = 4 + 4 + 16 + payload_len
        if limit - pos < frame:
            if allow_torn:
                return pos
            raise RustmapperResumeError("torn WAL record inside the checkpoint")
        expected = int.from_bytes(data[pos + 4 : pos + 8], "little")
        seq = data[pos + 8 : pos + 24]
        payload = data[pos + 24 : pos + 24 + payload_len]
        actual = crc32c(seq + payload)
        if actual != expected:
            raise RustmapperResumeError("WAL CRC mismatch")
        pos += frame
    return pos
