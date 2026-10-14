"""Daily Parquet snapshots of URL scores for offline analysis (#1103).

Reads a URL list or JSONL records, scores them with the static
``URLValueAssessor`` (no historical lake, no Redis), and writes
``scoring_YYYYMMDD.parquet``. Optional ``publication_date`` values also
store ``scoring_metrics.calculate_decay_score`` inside ``features``.

Does not crawl and does not read Redis, Kafka, or the Delta lake.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from src.utils.url_canon import url_hash as pipeline_url_hash

COLUMNS: tuple[str, ...] = ("url_hash", "score", "features", "stage", "ts")
ARCHIVE_NAME = "scoring_archive.parquet"
DEFAULT_KEEP_DAYS = 14
DEFAULT_STAGE = "stage1"
DEFAULT_OUTPUT_DIR = "data/exports"

_DAILY_NAME = re.compile(r"^scoring_(\d{8})\.parquet$")
_HASH = re.compile(r"^[0-9a-f]{16}$")
_STAGE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_PROTECTED = frozenset({"delta_lake", "dlq", "kafka_spill"})


@dataclass(frozen=True)
class ScoringExportResult:
    path: Path
    rows: int
    skipped: int
    compacted_files: int
    archive_path: Path | None
    errors: tuple[str, ...]
    written: bool


def snapshot_schema():
    """Physical Parquet schema. ``features`` is one UTF-8 JSON object."""
    import pyarrow as pa

    return pa.schema(
        [
            ("url_hash", pa.string()),
            ("score", pa.float64()),
            ("features", pa.string()),
            ("stage", pa.string()),
            ("ts", pa.timestamp("us", tz="UTC")),
        ]
    )


def snapshot_filename(day: date) -> str:
    return f"scoring_{day.strftime('%Y%m%d')}.parquet"


def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--urls",
        action="append",
        default=None,
        metavar="FILE",
        help="Text file of URLs, one per line ('#' comments and blank lines skipped). '-' reads stdin. Repeatable.",
    )
    parser.add_argument(
        "--jsonl",
        action="append",
        default=None,
        metavar="FILE",
        help="JSONL records (url, score, features, stage, ts, publication_date). '-' reads stdin. Repeatable.",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for scoring_YYYYMMDD.parquet (default {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--day",
        type=_parse_day,
        default=None,
        help="Snapshot date YYYYMMDD or YYYY-MM-DD (default UTC today). Selects the filename, not each row's ts.",
    )
    parser.add_argument("--stage", type=_parse_stage, default=DEFAULT_STAGE, help="Stage stored when a row omits one")
    parser.add_argument("--ts", default=None, help="ISO-8601 timestamp used when a row omits ts (default UTC now)")
    parser.add_argument(
        "--keep-days",
        type=_non_negative_int,
        default=DEFAULT_KEEP_DAYS,
        help=f"Keep this many UTC days of scoring_YYYYMMDD.parquet, then fold older files into {ARCHIVE_NAME}",
    )
    parser.add_argument(
        "--no-compact",
        action="store_true",
        help=f"Do not fold old daily files into {ARCHIVE_NAME}",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Replace today's file instead of merging rows into it",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a daily URL-scoring Parquet snapshot for offline analysis (#1103). "
        "Does not crawl and does not read Redis or the Delta lake.",
    )
    add_cli_arguments(parser)
    args = parser.parse_args(argv)
    try:
        return run_cli(args)
    except OSError as exc:
        print(f"scoring snapshot failed: {exc}", file=_stderr())
        return 1
    except ValueError as exc:
        print(f"scoring snapshot failed: {exc}", file=_stderr())
        return 1


def run_cli(args: argparse.Namespace) -> int:
    url_specs = list(args.urls or [])
    jsonl_specs = list(args.jsonl or [])
    if not url_specs and not jsonl_specs:
        print("scoring snapshot: pass --urls and/or --jsonl", file=_stderr())
        return 2
    if sum(spec == "-" for spec in url_specs + jsonl_specs) > 1:
        print("scoring snapshot: stdin ('-') can be used only once", file=_stderr())
        return 2

    urls: list[str] = []
    records: list[dict] = []
    errors: list[str] = []
    attempted = 0
    for spec in url_specs:
        text = _read_text_source(spec)
        url_rows, url_count = _urls_from_text(text)
        urls.extend(url_rows)
        attempted += url_count
    for spec in jsonl_specs:
        text = _read_text_source(spec)
        jsonl_rows, parse_errors, jsonl_count = _records_from_jsonl(text)
        records.extend(jsonl_rows)
        errors.extend(parse_errors)
        attempted += jsonl_count

    default_ts = _as_utc(args.ts, datetime.now(UTC)) if args.ts else None
    result = export_scoring_snapshot(
        urls=urls,
        records=records,
        output_dir=args.output_dir,
        day=args.day,
        stage=args.stage,
        ts=default_ts,
        keep_days=args.keep_days,
        compact=not args.no_compact,
        merge=not args.no_merge,
        extra_errors=errors,
        attempted=attempted,
    )
    if not result.written:
        print("scoring snapshot: no rows written", file=_stderr())
        _print_errors(result.errors)
        return 1
    archive = f" archive={result.archive_path}" if result.archive_path else ""
    print(
        f"scoring snapshot: {result.path} rows={result.rows} skipped={result.skipped} "
        f"compacted={result.compacted_files}{archive}"
    )
    if result.errors:
        _print_errors(result.errors)
    return 0


def export_scoring_snapshot(
    *,
    urls: list[str] | None = None,
    records: list[dict] | None = None,
    output_dir: str | Path,
    day: date | None = None,
    stage: str = DEFAULT_STAGE,
    ts: datetime | None = None,
    keep_days: int = DEFAULT_KEEP_DAYS,
    compact: bool = True,
    merge: bool = True,
    extra_errors: list[str] | None = None,
    attempted: int | None = None,
) -> ScoringExportResult:
    """Write one daily snapshot and, by default, compact older daily files.

    Rows that fail validation are skipped and listed on ``errors``. When every
    attempted row fails, nothing is written and compaction does not run.
    """
    if not _STAGE.fullmatch(stage):
        raise ValueError("stage must match [A-Za-z0-9_.:-]{1,64}")
    if keep_days < 0:
        raise ValueError("keep_days must be >= 0")

    now = datetime.now(UTC)
    snapshot_day = day or now.date()
    default_ts = ts or now
    if default_ts.tzinfo is None:
        default_ts = default_ts.replace(tzinfo=UTC)
    else:
        default_ts = default_ts.astimezone(UTC)

    url_rows = list(urls or [])
    record_rows = list(records or [])
    errors = list(extra_errors or [])
    if attempted is None:
        attempted = len(url_rows) + len(record_rows) + len(errors)

    out_dir = Path(output_dir)
    _assert_output_allowed(out_dir)
    destination = out_dir / snapshot_filename(snapshot_day)
    _assert_output_allowed(destination)

    assessor = None

    def get_assessor():
        nonlocal assessor
        if assessor is None:
            from src.common.url_value_assessor import URLValueAssessor

            assessor = URLValueAssessor(use_historical_data=False)
        return assessor

    built: list[dict] = []
    for url in url_rows:
        try:
            built.append(_row_from_record({"url": url}, assessor=get_assessor(), default_stage=stage, default_ts=default_ts))
        except (TypeError, ValueError) as exc:
            errors.append(f"{url[:120]}: {exc}")
    for index, record in enumerate(record_rows, start=1):
        try:
            if not isinstance(record, dict):
                raise ValueError("record must be an object")
            built.append(_row_from_record(record, assessor=get_assessor(), default_stage=stage, default_ts=default_ts))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"record {index}: {exc}")

    if attempted > 0 and not built:
        return ScoringExportResult(
            path=destination,
            rows=0,
            skipped=len(errors),
            compacted_files=0,
            archive_path=None,
            errors=tuple(errors),
            written=False,
        )

    existing: list[dict] = []
    if merge and destination.is_file() and not destination.is_symlink():
        existing = _read_rows(destination)
    rows = _dedupe_rows(existing + built)
    _atomic_write(destination, _table_from_rows(rows))

    compacted = 0
    archive_path: Path | None = None
    if compact:
        compacted, archive_path = compact_old_snapshots(
            out_dir,
            today=snapshot_day,
            keep_days=keep_days,
            protect=destination,
        )
    return ScoringExportResult(
        path=destination,
        rows=len(rows),
        skipped=len(errors),
        compacted_files=compacted,
        archive_path=archive_path,
        errors=tuple(errors),
        written=True,
    )


def compact_old_snapshots(
    output_dir: Path,
    *,
    today: date,
    keep_days: int,
    protect: Path | None = None,
) -> tuple[int, Path | None]:
    """Fold ``scoring_YYYYMMDD.parquet`` files older than ``keep_days`` into the archive.

    Only regular files in ``output_dir`` whose names match that pattern are
    eligible. ``today``'s file, ``scoring_archive.parquet``, symlinks, and
    every other name are left in place. Nothing is deleted if a read fails.
    """
    if keep_days < 0:
        raise ValueError("keep_days must be >= 0")
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return 0, None
    _assert_output_allowed(output_dir)
    cutoff = today - timedelta(days=keep_days)
    protected = protect.resolve() if protect is not None else None
    archive = output_dir / ARCHIVE_NAME
    old: list[Path] = []
    if output_dir.is_dir():
        for path in sorted(output_dir.iterdir(), key=lambda item: item.name):
            scored_day = _daily_date(path)
            if scored_day is None or scored_day >= cutoff:
                continue
            if protected is not None and path.resolve() == protected:
                continue
            old.append(path)
    if not old:
        return 0, archive if archive.is_file() else None

    rows: list[dict] = []
    if archive.is_file() and not archive.is_symlink():
        rows.extend(_read_rows(archive))
    for path in old:
        rows.extend(_read_rows(path))
    _atomic_write(archive, _table_from_rows(_dedupe_rows(rows)))
    for path in old:
        path.unlink()
    return len(old), archive


def _row_from_record(record: dict, *, assessor, default_stage: str, default_ts: datetime) -> dict:
    url = record.get("url")
    if url is not None and not isinstance(url, str):
        raise ValueError("url must be a string")
    if isinstance(url, str):
        url = url.strip() or None

    given_hash = record.get("url_hash")
    if given_hash is not None:
        if not isinstance(given_hash, str) or _HASH.fullmatch(given_hash.lower()) is None:
            raise ValueError("url_hash must be 16 hex characters")
        given_hash = given_hash.lower()

    if url:
        computed = pipeline_url_hash(url)
        if given_hash and given_hash != computed:
            raise ValueError("url_hash does not match the canonical url")
        hashed = computed
    elif given_hash:
        hashed = given_hash
    else:
        raise ValueError("row needs a url or url_hash")

    stage_value = record.get("stage", default_stage)
    if not isinstance(stage_value, str) or _STAGE.fullmatch(stage_value) is None:
        raise ValueError("invalid stage")

    explicit_score = "score" in record and record.get("score") is not None
    features = _features_obj(record.get("features"))
    if explicit_score:
        score = _finite_score(record.get("score"))
        if url and "url" not in features:
            features = {**features, "url": url}
    else:
        if not url:
            raise ValueError("row needs a url to score")
        depth = _depth(record.get("depth", 0))
        js_confidence = _js_confidence(record.get("js_confidence", 0.0))
        assessed = assessor.assess_url(url, depth=depth, js_confidence=js_confidence)
        score = float(assessed.value_score)
        assessed_features = {
            "url": url,
            "value_score": assessed.value_score,
            "content_likelihood": assessed.content_likelihood,
            "recommended_spider": assessed.recommended_spider,
            "reasons": list(assessed.reasons),
            "metadata": dict(assessed.metadata),
            "depth": depth,
            "js_confidence": js_confidence,
        }
        assessed_features.update(features)
        features = assessed_features

    publication_date = record.get("publication_date")
    if publication_date is not None:
        if not isinstance(publication_date, (str, datetime)):
            raise ValueError("publication_date must be an ISO string or datetime")
        from src.common.scoring_metrics import calculate_decay_score

        row_ts = _as_utc(record.get("ts"), default_ts)
        decay = calculate_decay_score(publication_date, reference_date=row_ts)
        stored_date = publication_date if isinstance(publication_date, str) else publication_date.isoformat()
        features = {**features, "publication_date": stored_date, "decay_score": decay}
    else:
        row_ts = _as_utc(record.get("ts"), default_ts)

    return {
        "url_hash": hashed,
        "score": score,
        "features": _dump_features(features),
        "stage": stage_value,
        "ts": row_ts,
    }


def _features_obj(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("features must be a JSON object")
        return parsed
    raise ValueError("features must be a JSON object")


def _dump_features(features: dict) -> str:
    return json.dumps(features, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _finite_score(value) -> float:
    if isinstance(value, bool) or isinstance(value, (list, dict)) or value is None:
        raise ValueError("score must be a finite number")
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("score must be a finite number") from exc
    if not math.isfinite(score):
        raise ValueError("score must be a finite number")
    return score


def _depth(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _js_confidence(value) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(parsed):
        return 0.0
    return min(max(parsed, 0.0), 1.0)


def _as_utc(value, default: datetime) -> datetime:
    if value is None:
        return default
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("ts must be ISO-8601") from exc
    else:
        raise ValueError("ts must be ISO-8601")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    best: dict[tuple[str, str], dict] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (row["url_hash"], row["stage"])
        previous = best.get(key)
        if previous is None:
            order.append(key)
            best[key] = row
            continue
        if row["ts"] >= previous["ts"]:
            best[key] = row
    return [best[key] for key in order]


def _table_from_rows(rows: list[dict]):
    import pyarrow as pa

    schema = snapshot_schema()
    if not rows:
        return schema.empty_table()
    columns = {
        "url_hash": pa.array([row["url_hash"] for row in rows], type=pa.string()),
        "score": pa.array([row["score"] for row in rows], type=pa.float64()),
        "features": pa.array([row["features"] for row in rows], type=pa.string()),
        "stage": pa.array([row["stage"] for row in rows], type=pa.string()),
        "ts": pa.array([row["ts"] for row in rows], type=pa.timestamp("us", tz="UTC")),
    }
    return pa.table(columns, schema=schema)


def _read_rows(path: Path) -> list[dict]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        table = pq.read_table(path, columns=list(COLUMNS))
        table = table.cast(snapshot_schema())
    except (OSError, pa.ArrowInvalid, pa.ArrowTypeError, ValueError) as exc:
        raise ValueError(f"{path.name} does not match the scoring snapshot schema") from exc
    return [_normalize_row(row) for row in table.to_pylist()]


def _normalize_row(row: dict) -> dict:
    ts = row.get("ts")
    if not isinstance(ts, datetime):
        raise ValueError("snapshot ts is not a timestamp")
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    else:
        ts = ts.astimezone(UTC)
    url_hash = row.get("url_hash")
    stage = row.get("stage")
    features = row.get("features")
    score = row.get("score")
    if not isinstance(url_hash, str) or not isinstance(stage, str) or not isinstance(features, str):
        raise ValueError("snapshot row has a null field")
    if isinstance(score, bool) or score is None or not math.isfinite(float(score)):
        raise ValueError("snapshot score is missing")
    return {
        "url_hash": url_hash,
        "score": float(score),
        "features": features,
        "stage": stage,
        "ts": ts,
    }


def _atomic_write(path: Path, table) -> None:
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        pq.write_table(table, temporary, compression="snappy")
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _daily_date(path: Path) -> date | None:
    if not path.is_file() or path.is_symlink():
        return None
    match = _DAILY_NAME.fullmatch(path.name)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _assert_output_allowed(path: Path) -> None:
    parts = path.resolve().parts
    for index in range(len(parts) - 1):
        if parts[index] == "data" and parts[index + 1] in _PROTECTED:
            raise ValueError(f"refusing to write scoring snapshots under {path.resolve()}")


def _urls_from_text(text: str) -> tuple[list[str], int]:
    urls: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        urls.append(stripped)
    return urls, len(urls)


def _records_from_jsonl(text: str) -> tuple[list[dict], list[str], int]:
    records: list[dict] = []
    errors: list[str] = []
    attempted = 0
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        attempted += 1
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"jsonl line {index}: {exc.msg}")
            continue
        if not isinstance(parsed, dict):
            errors.append(f"jsonl line {index}: expected an object")
            continue
        records.append(parsed)
    return records, errors, attempted


def _read_text_source(spec: str) -> str:
    import sys

    if spec == "-":
        return sys.stdin.read()
    path = Path(spec)
    if not path.is_file():
        raise FileNotFoundError(spec)
    return path.read_text(encoding="utf-8")


def _parse_day(value: str) -> date:
    text = value.strip()
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("day must be YYYYMMDD or YYYY-MM-DD") from exc


def _parse_stage(value: str) -> str:
    if _STAGE.fullmatch(value) is None:
        raise argparse.ArgumentTypeError("stage must be 1-64 characters from [A-Za-z0-9_.:-]")
    return value


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def _print_errors(errors: tuple[str, ...] | list[str]) -> None:
    shown = list(errors[:20])
    for item in shown:
        print(f"skipped: {item}", file=_stderr())
    remaining = len(errors) - len(shown)
    if remaining > 0:
        print(f"skipped: and {remaining} more", file=_stderr())


def _stderr():
    import sys

    return sys.stderr
