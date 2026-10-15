"""Contract between rustmapper ``sitemap.jsonl`` and SeedManager (#936).

Rust-sitemap's public JSONL line (README, and the shape #574 maps into
seeds) is one object with exactly these fields, in this order::

    {"url","depth","status_code","content_length","title","link_count"}

``cargo test`` in ``rustmapper_contract/`` emits that shape. An unknown key,
a missing key, or a wrong JSON type is an incompatible field and must fail
before SeedManager sees the row. Schema-valid lines are handed to
``SeedManager.add_urls_to_seeds`` with ``source_spider="rustmapper"``;
SeedManager accepts or rejects the URL (http/https with a host, no
whitespace, under 2048 characters). This module does not crawl.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Declaration order is the emitted JSON key order. Cargo uses the same order.
RUSTMAPPER_JSONL_FIELDS: tuple[str, ...] = (
    "url",
    "depth",
    "status_code",
    "content_length",
    "title",
    "link_count",
)

# Match the Rust struct widths so a value one side accepts is not rejected
# by the other: depth and link_count are u32, status_code is u16,
# content_length is u64.
_UINT_MAX: dict[str, int] = {
    "depth": 2**32 - 1,
    "status_code": 2**16 - 1,
    "content_length": 2**64 - 1,
    "link_count": 2**32 - 1,
}

RUSTMAPPER_SOURCE_SPIDER = "rustmapper"

# Columns SeedManager writes on seed_urls (see seed_manager.SeedManager).
SEED_ROW_FIELDS: tuple[str, ...] = (
    "url",
    "url_hash",
    "discovered_at",
    "source_url",
    "source_spider",
)


class RustmapperContractError(ValueError):
    """A JSONL line is not the rustmapper record SeedManager is willing to read."""


def _incompatible(field: str) -> RustmapperContractError:
    return RustmapperContractError(f"incompatible field: {field}")


def parse_rustmapper_record(obj: object) -> dict[str, Any]:
    """Return the six contract fields, or raise if the object drifted.

    The returned dict keeps ``RUSTMAPPER_JSONL_FIELDS`` order so
    ``emit_jsonl_line`` matches the cargo emission byte for byte.
    """
    if not isinstance(obj, dict):
        raise _incompatible("record")
    unknown = [key for key in obj if key not in RUSTMAPPER_JSONL_FIELDS]
    if unknown:
        raise _incompatible(unknown[0])
    missing = [key for key in RUSTMAPPER_JSONL_FIELDS if key not in obj]
    if missing:
        raise _incompatible(missing[0])

    url = obj["url"]
    if not isinstance(url, str):
        raise _incompatible("url")
    title = obj["title"]
    if not isinstance(title, str):
        raise _incompatible("title")

    parsed: dict[str, Any] = {"url": url, "title": title}
    for key, limit in _UINT_MAX.items():
        value = obj[key]
        # bool is an int subclass; JSON true/false is not a rustmapper integer.
        if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > limit:
            raise _incompatible(key)
        parsed[key] = value

    return {key: parsed[key] for key in RUSTMAPPER_JSONL_FIELDS}


def emit_jsonl_line(record: Mapping[str, Any]) -> str:
    """Serialize one record the way ``cargo test`` emits it (compact, key order)."""
    parsed = parse_rustmapper_record(dict(record))
    return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)


def load_rustmapper_jsonl(path: str | Path) -> list[dict[str, Any]]:
    """Parse a sitemap.jsonl file. The first incompatible line fails the file."""
    text = Path(path).read_text(encoding="utf-8")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RustmapperContractError(f"line {index}: incompatible field: json") from exc
        try:
            records.append(parse_rustmapper_record(obj))
        except RustmapperContractError as exc:
            raise RustmapperContractError(f"line {index}: {exc}") from exc
    return records


def ingest_rustmapper_jsonl(
    seed_manager: Any,
    path: str | Path,
    *,
    source_url: str,
    enqueue_stage2: bool = False,
) -> dict[str, int]:
    """Validate ``path``, then let SeedManager accept or reject each URL.

    Does not pass ``write_domain_urls`` / ``write_uconn_urls``. The manager
    keeps its profile default (the bundled config leaves domain writes on).
    An incompatible field raises ``RustmapperContractError`` and writes nothing.
    """
    records = load_rustmapper_jsonl(path)
    result = seed_manager.add_urls_to_seeds(
        urls=[record["url"] for record in records],
        source_url=source_url,
        source_spider=RUSTMAPPER_SOURCE_SPIDER,
        enqueue_stage2=enqueue_stage2,
    )
    return dict(result)
