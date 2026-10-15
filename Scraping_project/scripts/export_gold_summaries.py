#!/usr/bin/env python3
"""Bulk-index Stage 3 and Stage 4 summaries into OpenSearch or Elasticsearch (#687).

Disabled by default. A run with no switch prints a notice, exits 0, and does
not read the lake or open a socket. Pass ``--enable`` or set
``SEARCH_EXPORT_ENABLED=1`` (see ``src/utils/feature_flags.py``).

The document shape is ``scripts/search/gold_summaries_mapping.json``. Compose
profile ``search`` can start a loopback OpenSearch; it is not in the default
``core,monitoring`` profiles. ``SEARCH_URL`` may point at that node or at
Elasticsearch. The bulk API used here is the shared subset. This script does
not scrape and does not write Delta.

Tables, in order: ``stage3_summaries``, legacy ``stage4_summaries`` (still
Stage 3 output), ``stage4_large_doc_summaries``. Missing tables are skipped.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAPPING_PATH = Path(__file__).resolve().parent / "search" / "gold_summaries_mapping.json"

GOLD_TABLES: tuple[str, ...] = (
    "stage3_summaries",
    "stage4_summaries",
    "stage4_large_doc_summaries",
)
MAPPED_FIELDS: frozenset[str] = frozenset({
    "url",
    "url_hash",
    "summary",
    "keywords",
    "word_count",
    "quality_score",
    "content_type",
    "original_size",
    "summary_size",
    "compression_ratio",
    "is_pdf",
    "stage",
    "source_table",
    "summarized_at",
})
GUARDED_DIRS: tuple[str, ...] = ("data/delta_lake", "data/dlq", "data/kafka_spill")
DEFAULT_URL = "http://127.0.0.1:9200"
DEFAULT_INDEX = "gold-summaries"
MAX_BATCH = 1000
FLAG = "SEARCH_EXPORT_ENABLED"

Transport = Callable[[str, str, bytes | None, Mapping[str, str]], "HttpResult"]


class ExportError(Exception):
    """Operator-facing failure. The message is safe to print."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: bytes


@dataclass
class ExportStats:
    enabled: bool
    dry_run: bool
    documents: int = 0
    skipped: int = 0
    indexed: int = 0
    errors: int = 0
    batches: int = 0
    index: str = DEFAULT_INDEX


def stage_for(table: str) -> str:
    """Stage label stored on the document. Legacy ``stage4_summaries`` is Stage 3."""
    if table == "stage4_large_doc_summaries":
        return "stage4"
    if table in {"stage3_summaries", "stage4_summaries"}:
        return "stage3"
    raise ExportError(f"unknown summary table: {table}")


def load_mapping(path: Path | None = None) -> dict[str, Any]:
    """Index-create body. Fails closed if the file drifts from the fields we send."""
    mapping_path = path or MAPPING_PATH
    try:
        raw = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"index mapping is not readable JSON: {mapping_path}") from exc
    if not isinstance(raw, dict):
        raise ExportError("index mapping must be a JSON object")
    settings = raw.get("settings")
    mappings = raw.get("mappings")
    if not isinstance(settings, dict) or settings.get("number_of_replicas") != 0:
        raise ExportError("index mapping must set number_of_replicas to 0")
    if not isinstance(mappings, dict) or mappings.get("dynamic") != "strict":
        raise ExportError("index mapping must set dynamic to strict")
    properties = mappings.get("properties")
    if not isinstance(properties, dict) or set(properties) != MAPPED_FIELDS:
        raise ExportError("index mapping properties do not match the exporter fields")
    return raw


def _flag_enabled() -> bool:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from src.utils.feature_flags import get_bool

    return get_bool(FLAG, False)


def _iso(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        stamped = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        millis = stamped.microsecond // 1000
        return stamped.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}Z"
    if isinstance(value, date):
        return value.isoformat() + "T00:00:00.000Z"
    if isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        seconds = number / 1000.0 if abs(number) >= 1_000_000_000_000 else number
        try:
            stamped = datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
        millis = stamped.microsecond // 1000
        return stamped.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}Z"
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _whole(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def _real(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _keywords(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or "\n" in text or "\r" in text:
        return None
    return text


def to_document(row: Mapping[str, Any], source_table: str) -> dict[str, Any] | None:
    """One search document, or None when the row has no indexable summary.

    Only mapped fields are copied. Page bodies and other lake columns stay out,
    because the index mapping is ``dynamic: strict``.
    """
    url = _text(row.get("url"))
    url_hash = _text(row.get("url_hash"))
    summary = row.get("summary")
    if url is None or url_hash is None or not isinstance(summary, str) or not summary.strip():
        return None
    doc: dict[str, Any] = {
        "url": url,
        "url_hash": url_hash,
        "summary": summary.strip(),
        "stage": stage_for(source_table),
        "source_table": source_table,
    }
    keywords = _keywords(row.get("keywords"))
    if keywords:
        doc["keywords"] = keywords
    for key in ("word_count", "original_size", "summary_size"):
        number = _whole(row.get(key))
        if number is not None:
            doc[key] = number
    for key in ("quality_score", "compression_ratio"):
        number = _real(row.get(key))
        if number is not None:
            doc[key] = number
    content_type = _text(row.get("content_type"))
    if content_type is not None:
        doc["content_type"] = content_type
    if isinstance(row.get("is_pdf"), bool):
        doc["is_pdf"] = row["is_pdf"]
    when = _iso(row.get("timestamp"))
    if when is None:
        when = _iso(row.get("processed_at"))
    if when is not None:
        doc["summarized_at"] = when
    return doc


def document_id(doc: Mapping[str, Any]) -> str:
    return f"{doc['source_table']}:{doc['url_hash']}"


def bulk_body(index: str, docs: Iterable[Mapping[str, Any]]) -> bytes:
    lines: list[str] = []
    for doc in docs:
        meta = {"index": {"_index": index, "_id": document_id(doc)}}
        lines.append(json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
        lines.append(json.dumps(doc, ensure_ascii=False, separators=(",", ":")))
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")


def _check_index_name(name: str) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789_-"
    if (
        not name
        or len(name) > 128
        or not name[0].islower()
        or any(ch not in alphabet for ch in name)
    ):
        raise ExportError("index name must match [a-z][a-z0-9_-]{0,127}")
    return name


def _check_search_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ExportError("SEARCH_URL must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ExportError("put credentials in SEARCH_USER and SEARCH_PASSWORD, not SEARCH_URL")
    return url.rstrip("/")


def _timeout() -> float:
    raw = os.environ.get("SEARCH_TIMEOUT", "").strip()
    if not raw:
        return 30.0
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    if value <= 0:
        return 30.0
    return min(value, 120.0)


def default_transport() -> Transport:
    user = os.environ.get("SEARCH_USER", "")
    password = os.environ.get("SEARCH_PASSWORD", "")
    timeout = _timeout()

    def transport(method: str, url: str, body: bytes | None, headers: Mapping[str, str]) -> HttpResult:
        merged = dict(headers)
        if user:
            token = base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
            merged["Authorization"] = f"Basic {token}"
        request = urllib.request.Request(url, data=body, headers=merged, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return HttpResult(response.status, response.read())
        except urllib.error.HTTPError as exc:
            return HttpResult(exc.code, exc.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise ExportError(f"search endpoint unreachable: {reason}") from exc

    return transport


def _snippet(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    return text[:300]


def _already_exists(body: bytes) -> bool:
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return b"resource_already_exists_exception" in body
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return error.get("type") == "resource_already_exists_exception"
    return "resource_already_exists_exception" in _snippet(body)


def ensure_index(base: str, index: str, transport: Transport, mapping: Mapping[str, Any]) -> None:
    head = transport("HEAD", f"{base}/{index}", None, {"Accept": "application/json"})
    if head.status == 200:
        return
    if head.status not in {404, 400}:
        raise ExportError(f"index lookup failed ({head.status}): {_snippet(head.body)}")
    body = json.dumps(mapping, separators=(",", ":")).encode("utf-8")
    created = transport(
        "PUT",
        f"{base}/{index}",
        body,
        {"Content-Type": "application/json", "Accept": "application/json"},
    )
    if 200 <= created.status < 300 or (created.status == 400 and _already_exists(created.body)):
        return
    raise ExportError(f"index create failed ({created.status}): {_snippet(created.body)}")


def _bulk_errors(body: bytes) -> tuple[int, int, str]:
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise ExportError(f"bulk response was not JSON: {_snippet(body)}") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ExportError(f"bulk response had no items: {_snippet(body)}")
    failed = 0
    reason = ""
    for item in items:
        action = {}
        if isinstance(item, dict):
            action = item.get("index") or item.get("create") or {}
        if not isinstance(action, dict):
            failed += 1
            continue
        status = int(action.get("status") or 0)
        if "error" in action or status >= 300:
            failed += 1
            if not reason:
                reason = _snippet(json.dumps(action.get("error", "")).encode())
    return len(items) - failed, failed, reason


def _refuse_guarded(path: Path) -> None:
    resolved = path.resolve()
    for rel in GUARDED_DIRS:
        base = (PROJECT_ROOT / rel).resolve()
        if resolved == base or base in resolved.parents:
            raise ExportError(f"refusing to write under {rel}")


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if value < 1 or value > MAX_BATCH:
        raise argparse.ArgumentTypeError(f"must be 1..{MAX_BATCH}")
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bulk-index gold summaries into OpenSearch or Elasticsearch (off by default, #687).",
    )
    parser.add_argument("--enable", action="store_true", help="run this once even if the flag is off")
    parser.add_argument("--dry-run", action="store_true", help="write bulk NDJSON and do not call the search API")
    parser.add_argument("--input", type=Path, help="JSONL rows instead of the Delta tables (each may set source_table)")
    parser.add_argument("--lake", help="Delta lake root (default DELTA_LAKE_PATH or data/delta_lake)")
    parser.add_argument("--output", type=Path, help="dry-run bulk body path; default is stdout")
    parser.add_argument("--index", default=os.environ.get("SEARCH_INDEX", DEFAULT_INDEX))
    parser.add_argument("--url", default=os.environ.get("SEARCH_URL", DEFAULT_URL))
    parser.add_argument("--batch-size", type=_positive_int, default=200)
    parser.add_argument("--limit", type=_positive_int, default=None, help="stop after this many documents")
    return parser.parse_args(argv)


def _iter_jsonl(path: Path) -> Iterator[tuple[str, Mapping[str, Any]]]:
    if not path.is_file():
        raise ExportError(f"input not found: {path}")
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ExportError(f"{path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ExportError(f"{path}:{line_no}: expected an object")
            table = row.get("source_table", "stage3_summaries")
            if not isinstance(table, str) or table not in GOLD_TABLES:
                raise ExportError(f"{path}:{line_no}: unknown source_table")
            yield table, row


def _lake_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("DELTA_LAKE_PATH")
    if env:
        return Path(env)
    return PROJECT_ROOT / "data" / "delta_lake"


def iter_delta_rows(lake: Path, table: str, batch_size: int) -> Iterator[dict[str, Any]]:
    """Stream one summary table. A missing table yields nothing and does not import deltalake."""
    path = lake / table
    if not (path / "_delta_log").is_dir():
        return
    try:
        from deltalake import DeltaTable
    except ImportError as exc:
        raise ExportError("deltalake is required to read summary tables") from exc
    dataset = DeltaTable(str(path)).to_pyarrow_dataset()
    scanner = dataset.scanner(batch_size=batch_size, batch_readahead=1, fragment_readahead=1)
    for batch in scanner.to_batches():
        for row in batch.to_pylist():
            if isinstance(row, dict):
                yield row


def _iter_rows(args: argparse.Namespace) -> Iterator[tuple[str, Mapping[str, Any]]]:
    if args.input is not None:
        yield from _iter_jsonl(args.input)
        return
    lake = _lake_root(args.lake)
    if not lake.is_dir():
        raise ExportError(f"lake not found: {lake}")
    for table in GOLD_TABLES:
        for row in iter_delta_rows(lake, table, args.batch_size):
            yield table, row


def _flush(
    batch: list[dict[str, Any]],
    *,
    index: str,
    dry_run: bool,
    base: str,
    transport: Transport | None,
    sink: Any,
    stats: ExportStats,
) -> None:
    payload = bulk_body(index, batch)
    stats.batches += 1
    stats.documents += len(batch)
    if dry_run:
        sink.write(payload)
        stats.indexed += len(batch)
        return
    assert transport is not None
    result = transport(
        "POST",
        f"{base}/_bulk",
        payload,
        {"Content-Type": "application/x-ndjson", "Accept": "application/json"},
    )
    if not 200 <= result.status < 300:
        raise ExportError(f"bulk request failed ({result.status}): {_snippet(result.body)}")
    ok, failed, reason = _bulk_errors(result.body)
    if ok + failed != len(batch):
        raise ExportError(f"bulk response counted {ok + failed} items, sent {len(batch)}")
    stats.indexed += ok
    stats.errors += failed
    if reason:
        print(f"bulk item error: {reason}", file=sys.stderr)


def _ready_to_read(args: argparse.Namespace) -> None:
    """Fail before any socket when the chosen input is missing."""
    if args.input is not None:
        if not args.input.is_file():
            raise ExportError(f"input not found: {args.input}")
        return
    lake = _lake_root(args.lake)
    if not lake.is_dir():
        raise ExportError(f"lake not found: {lake}")


def execute(args: argparse.Namespace, transport: Transport | None = None) -> ExportStats:
    enabled = bool(args.enable) or _flag_enabled()
    stats = ExportStats(enabled=enabled, dry_run=bool(args.dry_run), index=args.index)
    if not enabled:
        return stats
    stats.index = _check_index_name(str(args.index))
    if args.output is not None and not args.dry_run:
        raise ExportError("--output is only valid with --dry-run")
    mapping = load_mapping()
    _ready_to_read(args)
    sink = None
    if args.dry_run and args.output is not None:
        _refuse_guarded(args.output)
        if not args.output.parent.is_dir():
            raise ExportError(f"output directory not found: {args.output.parent}")
        sink = args.output.open("wb")
    elif args.dry_run:
        sink = sys.stdout.buffer
    base = ""
    if not args.dry_run:
        base = _check_search_url(str(args.url))
        if transport is None:
            transport = default_transport()
        ensure_index(base, stats.index, transport, mapping)
    try:
        batch: list[dict[str, Any]] = []
        for table, row in _iter_rows(args):
            doc = to_document(row, table)
            if doc is None:
                stats.skipped += 1
                continue
            if args.limit is not None and stats.documents + len(batch) >= args.limit:
                break
            batch.append(doc)
            if len(batch) >= args.batch_size:
                _flush(
                    batch,
                    index=stats.index,
                    dry_run=args.dry_run,
                    base=base,
                    transport=transport,
                    sink=sink,
                    stats=stats,
                )
                batch = []
        if batch:
            _flush(
                batch,
                index=stats.index,
                dry_run=args.dry_run,
                base=base,
                transport=transport,
                sink=sink,
                stats=stats,
            )
    finally:
        if sink is not None and sink is not sys.stdout.buffer:
            sink.close()
    return stats


def _summary(stats: ExportStats) -> str:
    return json.dumps({
        "enabled": stats.enabled,
        "dry_run": stats.dry_run,
        "index": stats.index,
        "documents": stats.documents,
        "indexed": stats.indexed,
        "skipped": stats.skipped,
        "errors": stats.errors,
        "batches": stats.batches,
    })


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        stats = execute(args)
    except ExportError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not stats.enabled:
        print(f"search export disabled; pass --enable or set {FLAG}=1 (#687)")
        return 0
    line = _summary(stats)
    if stats.dry_run and args.output is None:
        sys.stdout.buffer.flush()
        print(line, file=sys.stderr)
    else:
        print(line)
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
