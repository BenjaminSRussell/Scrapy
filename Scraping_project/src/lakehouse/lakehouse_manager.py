import logging
import os
import queue
import signal
import sys
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias

from src.core.config import Config
from src.scrapy_prometheus import (
    DELTA_MANAGER_CONTEXT_ENTER_TOTAL,
    DELTA_MANAGER_CONTEXT_EXIT_TOTAL,
    DELTA_MANAGER_SHUTDOWN_DURATION_SECONDS,
    DELTA_MANAGER_SHUTDOWN_TOTAL,
)

try:
    import pyarrow as pa
    import pyarrow.csv as pa_csv
    import pyarrow.parquet as pq
    from deltalake import DeltaTable, WriterProperties, write_deltalake

    DELTA_AVAILABLE = True
except ImportError:
    DELTA_AVAILABLE = False
    DeltaTable = None  # type: ignore
    write_deltalake = None
    WriterProperties = None  # type: ignore
    pa = None
    pa_csv = None
    pq = None

logger = logging.getLogger(__name__)

CAST_QUARANTINE_TABLE = "cast_quarantine"
# Rows of domain-partitioned tables whose URL has no usable host (#458). They
# used to be written under domain="unknown", one hot, skewed partition.
DOMAIN_QUARANTINE_TABLE = "domain_quarantine"
CastMode: TypeAlias = Literal["strict", "coerce"]

try:  # cast failures by table/column (#818)
    from prometheus_client import Counter as _Counter

    DELTA_CAST_FAILURES = _Counter(
        "delta_cast_failures_total",
        "Values that failed to cast to the cached Arrow schema, by table and column.",
        ["table", "column"],
    )
except Exception:  # prometheus_client missing or metric already registered
    DELTA_CAST_FAILURES = None

try:  # undomainable rows quarantined instead of partitioned as "unknown" (#458)
    from prometheus_client import Counter as _DCounter

    DELTA_UNKNOWN_DOMAIN = _DCounter(
        "delta_unknown_domain_rows_total",
        "Rows for domain-partitioned tables quarantined because their URL has no usable host.",
        ["table"],
    )
except Exception:  # prometheus_client missing or metric already registered
    DELTA_UNKNOWN_DOMAIN = None

try:  # async write durability (#225) and queue backpressure (#167)
    from prometheus_client import Counter as _WCounter
    from prometheus_client import Gauge as _WGauge

    DELTA_WRITE_FAILURES = _WCounter(
        "delta_write_failures_total",
        "Failed Delta write attempts, by table and outcome (retry|spilled).",
        ["table", "outcome"],
    )
    DELTA_WRITE_QUEUE_FULL = _WCounter(
        "delta_write_queue_full_total",
        "Async writes that hit a full write queue and were spilled instead of blocking.",
        ["table"],
    )
    DELTA_WRITE_QUEUE_DEPTH = _WGauge(
        "delta_write_queue_depth", "Batches waiting in the async Delta write queue."
    )
except Exception:
    DELTA_WRITE_FAILURES = DELTA_WRITE_QUEUE_FULL = DELTA_WRITE_QUEUE_DEPTH = None

try:  # Delta log checkpoints (#274)
    from prometheus_client import Counter as _CCounter

    DELTA_CHECKPOINTS = _CCounter(
        "delta_checkpoints_total",
        "Explicit Delta log checkpoints, by table and outcome (created|failed).",
        ["table", "outcome"],
    )
except Exception:
    DELTA_CHECKPOINTS = None

SPILL_DIR_NAME = "_write_spill"
CHECKPOINT_INTERVAL_PROPERTY = "delta.checkpointInterval"


def last_checkpoint_version(table_path: Path) -> int | None:
    """Version recorded in ``_delta_log/_last_checkpoint``, or None if absent/unreadable."""
    import json as _json

    marker = Path(table_path) / "_delta_log" / "_last_checkpoint"
    try:
        return int(_json.loads(marker.read_text())["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None

# merge_into (#169): real Delta MERGE, retried on optimistic-concurrency conflicts.
MERGE_MAX_ATTEMPTS = 6
MERGE_RETRY_BACKOFF_SECONDS = 0.05

try:
    from prometheus_client import Counter as _MCounter

    DELTA_MERGE_FAILURES = _MCounter(
        "delta_merge_failures_total",
        "merge_into calls that failed after retries (nothing committed), by table.",
        ["table"],
    )
except Exception:
    DELTA_MERGE_FAILURES = None


def _dedupe_by_key(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    """Keep the last row per key; MERGE rejects multiple source rows matching one target row."""
    by_key: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        by_key[tuple(row.get(k) for k in keys)] = row
    return list(by_key.values())


def _source_table(rows: list[dict[str, Any]], target_schema: Any) -> Any:
    """Arrow table for ``rows``, casting columns the target already has to its types."""
    columns: list[str] = []
    for row in rows:
        for col in row:
            if col not in columns:
                columns.append(col)
    target_types = {f.name: f.type for f in target_schema}
    arrays = []
    for col in columns:
        arr = pa.array([row.get(col) for row in rows])
        want = target_types.get(col)
        if want is not None and arr.type != want:
            try:
                arr = arr.cast(want)
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                pass  # let MERGE report the mismatch
        arrays.append(arr)
    return pa.Table.from_arrays(arrays, names=columns)


def cast_rows_to_schema(
    rows: list[dict[str, Any]], schema: Any, mode: str = "strict"
) -> tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    """Build an Arrow table for ``rows`` under ``schema`` without poisoning the batch (#818).

    Fast path: one vectorized cast per column. If any column fails, each row is
    cast individually. ``strict`` drops rows with any bad value; ``coerce``
    nulls bad values in nullable columns and keeps the row (non-nullable
    failures are still dropped).

    Returns ``(table_or_None, kept_rows, failures)``. Each failure carries the
    row index, column, value, error and the row itself.
    """
    columns = {f.name: [row.get(f.name) for row in rows] for f in schema}
    # Non-nullable (required) fields must never be null-filled (#226).
    required_ok = all(v is not None for f in schema if not f.nullable for v in columns[f.name])
    if required_ok:
        try:
            arrays = [pa.array(columns[f.name], type=f.type) for f in schema]
            return pa.Table.from_arrays(arrays, schema=schema), rows, []
        except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError, OverflowError):
            pass

    kept: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        fixed = dict(row)
        drop = False
        for f in schema:
            value = row.get(f.name)
            if value is None and not f.nullable:
                failures.append(
                    {
                        "row_index": idx,
                        "column": f.name,
                        "value": None,
                        "error": "required (non-nullable) field is missing or null",
                        "row": row,
                    }
                )
                drop = True
                continue
            try:
                pa.array([value], type=f.type)
            except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError, OverflowError) as e:
                failures.append(
                    {"row_index": idx, "column": f.name, "value": value, "error": str(e), "row": row}
                )
                if mode == "coerce" and f.nullable:
                    fixed[f.name] = None
                else:
                    drop = True
        if not drop:
            kept.append(fixed)

    if not kept:
        return None, [], failures
    arrays = [pa.array([r.get(f.name) for r in kept], type=f.type) for f in schema]
    return pa.Table.from_arrays(arrays, schema=schema), kept, failures


def evolve_table(table: Any, rows: list[dict[str, Any]], known: set[str]) -> tuple[Any, list[str]]:
    """Append columns present in ``rows`` but not in ``known`` (additive evolution, #229).

    Types are inferred from the batch. Columns that are null in every row carry
    no data and no usable type, so they are skipped until a value shows up.
    Returns ``(table, added_column_names)``.
    """
    new_cols: list[str] = []
    for row in rows:
        for col in row:
            if col not in known and col not in new_cols:
                new_cols.append(col)
    added: list[str] = []
    for col in new_cols:
        arr = pa.array([row.get(col) for row in rows])
        if pa.types.is_null(arr.type):
            continue
        table = table.append_column(col, arr)
        added.append(col)
    return table, added


def infer_table(rows: list[dict[str, Any]]) -> Any:
    """Arrow table inferred from ``rows``, dropping all-null (untyped) columns."""
    table = pa.Table.from_pylist(rows)
    keep = [name for name, typ in zip(table.column_names, table.schema.types) if not pa.types.is_null(typ)]
    return table.select(keep)


try:  # schema evolution visibility (#226)
    from prometheus_client import Counter as _SCounter

    DELTA_SCHEMA_EVOLUTIONS = _SCounter(
        "delta_schema_evolutions_total",
        "Columns added to a Delta table by additive schema evolution, by table.",
        ["table"],
    )
except Exception:
    DELTA_SCHEMA_EVOLUTIONS = None

try:  # explicit destructive schema replacement (#509)
    from prometheus_client import Counter as _SOCounter

    DELTA_SCHEMA_OVERWRITES = _SOCounter(
        "delta_schema_overwrites_total",
        "Writes that replaced a Delta table's schema (explicit schema_overwrite=True), by table.",
        ["table"],
    )
except Exception:
    DELTA_SCHEMA_OVERWRITES = None

try:  # optimize/vacuum vs concurrent writers (#702)
    from prometheus_client import Counter as _MCCounter

    DELTA_MAINTENANCE_CONFLICTS = _MCCounter(
        "delta_maintenance_conflicts_total",
        "Delta maintenance commits (compact/z_order/vacuum) that lost a concurrent-commit race and were retried.",
        ["table", "operation"],
    )
except Exception:
    DELTA_MAINTENANCE_CONFLICTS = None


def metadata_row_count(table: Any) -> int | None:
    """Exact row count from the Delta log's per-file ``num_records`` stats (#372).

    Returns None when any live file lacks stats (caller falls back to parquet
    footers). Assumes no deletion vectors, which delta-rs does not write.
    """
    # delta-rs panics in get_add_actions() when a table has no live files (e.g.
    # after a full DELETE) and the panic poisons the table handle, so ask first.
    if not table.file_uris():
        return 0
    actions = pa.table(table.get_add_actions(flatten=True))
    if actions.num_rows == 0:
        return 0
    if "num_records" not in actions.column_names:
        return None
    counts = actions.column("num_records")
    if counts.null_count:
        return None
    return int(sum(counts.to_pylist()))  # one entry per data file


PARTITIONED_TABLES = {"stage1_discovery", "stage2_page_analysis"}


def _partition_columns(table_name: str) -> list[str] | None:
    return ["domain"] if table_name in PARTITIONED_TABLES else None


def partition_domain(url: Any) -> str | None:
    """Partition key for an http(s) URL, or None when it has no usable host (#458).

    registrable_domain() returns "unknown" for empty/unparsable input and passes
    other junk through (``"not a url"`` -> ``"not a url"``, ``mailto:x`` ->
    ``"mailto"``), so non-http(s) or host-less URLs are rejected here first.
    """
    from urllib.parse import urlparse

    from src.utils.validation import registrable_domain

    value = str(url or "").strip()
    try:
        parsed = urlparse(value)
        host = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not host:
        return None
    domain = registrable_domain(value)
    return None if not domain or domain == "unknown" else domain


WriteMode: TypeAlias = Literal["append", "overwrite", "error", "ignore"]
WriteTask: TypeAlias = tuple[str, list[dict[str, Any]], WriteMode]
# (task_type, table_name, retention_hours); retention_hours is only used by "vacuum".
MaintenanceTask: TypeAlias = tuple[str, str, int]

# Z-order columns per table (#272); override with delta_lake.z_order_columns.
DEFAULT_Z_ORDER_COLUMNS: dict[str, list[str]] = {
    "stage1_discovery": ["url_hash", "discovered_at"],
    "stage2_page_analysis": ["url_hash", "processed_at"],
}

try:
    from prometheus_client import Counter as _OptCounter

    DELTA_OPTIMIZE_SKIPPED = _OptCounter(
        "delta_optimize_skipped_total",
        "Optimize steps skipped or failed, by table and reason (zorder_missing_columns|zorder_failed|compact_failed).",
        ["table", "reason"],
    )
except Exception:  # prometheus_client missing or metric already registered
    DELTA_OPTIMIZE_SKIPPED = None


def _z_order_config(raw: Any) -> dict[str, list[str]]:
    """Validate the configured mapping; fall back to defaults on bad input."""
    if raw is None:
        return {k: list(v) for k, v in DEFAULT_Z_ORDER_COLUMNS.items()}
    if not isinstance(raw, dict):
        logger.warning(f"delta_lake.z_order_columns must be a mapping, got {type(raw).__name__}; using defaults")
        return {k: list(v) for k, v in DEFAULT_Z_ORDER_COLUMNS.items()}
    out: dict[str, list[str]] = {}
    for table, cols in raw.items():
        if isinstance(cols, (list, tuple)) and all(isinstance(c, str) and c for c in cols):
            out[str(table)] = list(cols)
        elif cols in (None, [], ()):
            out[str(table)] = []  # explicit opt-out for this table
        else:
            logger.warning(f"Ignoring invalid z_order_columns for {table}: {cols!r}")
    return out


TRASH_DIR = "_trash"
AUDIT_DIR = "_audit"


def table_file_stats(table: Any) -> tuple[int, int]:
    """(live data files, distinct partitions) from the Delta log's add actions (#600)."""
    if not table.file_uris():  # see metadata_row_count: empty tables panic in delta-rs
        return 0, 0
    actions = pa.table(table.get_add_actions(flatten=True))
    if actions.num_rows == 0:
        return 0, 0
    part_cols = [c for c in actions.column_names if c.startswith("partition.")]
    if not part_cols:
        return actions.num_rows, 0
    keys = set(zip(*(actions.column(c).to_pylist() for c in part_cols)))
    return actions.num_rows, len(keys)

MEMORY_HISTORY_DEPTH = 10  # InMemoryBackend versions retained per table (#484)

MAINTENANCE_MAX_ATTEMPTS = 4
MAINTENANCE_RETRY_BACKOFF = 0.2  # seconds, doubled per attempt


def _maintenance_failure_reason(step: str, error: Exception) -> str:
    """``<step>_conflict`` when retries ran out on commit conflicts, else ``<step>_failed``."""
    try:
        from deltalake.exceptions import CommitFailedError
    except Exception:  # pragma: no cover
        return f"{step}_failed"
    return f"{step}_conflict" if isinstance(error, CommitFailedError) else f"{step}_failed"


EXPORT_FORMATS = ("csv", "json", "parquet")
EXPORT_DEFAULT_BATCH_SIZE = 65_536
EXPORT_BATCH_READAHEAD = 2


def _export_part_path(base: Path, index: int) -> Path:
    return base.with_name(f"{base.stem}.part-{index:05d}{base.suffix}")


class _ExportSink:
    """One export output file, written batch by batch (#373)."""

    def __init__(self, path: Path, schema: Any, format: str):
        import pyarrow as pa
        import pyarrow.csv as pa_csv
        import pyarrow.parquet as pq

        self.path = path
        self.format = format
        self.rows = 0
        self._writer: Any = None
        if format == "json":
            self._stream: Any = open(path, "w", encoding="utf-8")
        else:
            self._stream = pa.OSFile(str(path), "wb")
            if format == "csv":
                self._writer = pa_csv.CSVWriter(self._stream, schema)
            else:
                self._writer = pq.ParquetWriter(self._stream, schema, compression="ZSTD")

    def write(self, batch: Any) -> None:
        if batch.num_rows == 0:
            return
        if self.format == "json":
            text = batch.to_pandas().to_json(orient="records", lines=True)
            self._stream.write(text if text.endswith("\n") else text + "\n")
        else:
            self._writer.write_batch(batch)
        self.rows += batch.num_rows

    def bytes_written(self) -> int:
        return int(self._stream.tell())

    def close(self) -> None:
        try:
            if self._writer is not None:
                self._writer.close()
        finally:
            self._stream.close()


class LakehouseManager:

    def __init__(self, base_path: str | None = None, start_workers: bool = True):

        config = Config.get_instance()

        if base_path is None:
            # Same contract as DeltaHelper (src/utils/delta.py): DELTA_LAKE_PATH
            # wins so compose/k8s workers and the metrics exporter share a lake.
            base_path = os.getenv("DELTA_LAKE_PATH") or config.get(
                "delta_lake.base_path", "./data/delta_lake"
            )
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        # #818: "strict" quarantines rows with uncastable values; "coerce"
        # nulls the bad value (nullable columns) and keeps the row.
        self.cast_mode = str(config.get("delta_lake.cast_mode", "strict")).lower()
        self.z_order_columns = _z_order_config(config.get("delta_lake.z_order_columns", None))

        self.tables = {
            "seed_urls": self.base_path / "seed_urls",
            "uconn_urls": self.base_path / "uconn_urls",
            "stage1_discovery": self.base_path / "stage1_discovery",
            "stage1_errors": self.base_path / "stage1_errors",
            "stage1_offsite_candidates": self.base_path / "stage1_offsite_candidates",
            "js_spider_queue": self.base_path / "js_spider_queue",
            "stage2_queue": self.base_path / "stage2_queue",
            "stage2_page_analysis": self.base_path / "stage2_page_analysis",
            "stage2_errors": self.base_path / "stage2_errors",
            "stage3_analytics": self.base_path / "stage3_analytics",
            "stage3_summaries": self.base_path / "stage3_summaries",
            "stage4_large_docs": self.base_path / "stage4_large_docs",
            "stage4_large_doc_summaries": self.base_path / "stage4_large_doc_summaries",
            "stage4_summaries": self.base_path / "stage4_summaries",
        }

        for table_path in self.tables.values():
            table_path.mkdir(parents=True, exist_ok=True)

        queue_maxsize = config.get("delta_lake.queue_maxsize", 1000)
        self.write_queue: queue.Queue[WriteTask | None] = queue.Queue(maxsize=queue_maxsize)
        # #167: producers wait at most this long for queue space, then spill.
        self.queue_put_timeout = float(config.get("delta_lake.queue_put_timeout_seconds", 30))
        # #225: async writes retry this many times (exponential backoff), then spill.
        self.write_retries = max(1, int(config.get("delta_lake.write_retries", 3)))
        self.write_retry_backoff = float(config.get("delta_lake.write_retry_backoff_seconds", 0.5))
        self.spill_path = self.base_path / SPILL_DIR_NAME
        # #274: delta-rs writes a log checkpoint every N commits, where N is the
        # table property delta.checkpointInterval. Keep it in sync with config.
        self.checkpoint_interval = max(1, int(config.get("delta_lake.checkpoint_interval", 100)))
        self._checkpoint_interval_synced: set[str] = set()

        self.schema_cache: dict[str, Any] = {}

        # Delta Lake commits are optimistic: two threads in this process
        # writing the same table race (e.g. both try to create version 0).
        # Serialize in-process writes per table; cross-process conflicts are
        # still handled by the commit retry loop in _write_sync_locked.
        self._table_locks: dict[str, threading.Lock] = {}
        self._table_locks_guard = threading.Lock()

        self.maintenance_queue: queue.Queue[MaintenanceTask | None] = queue.Queue()

        self.worker_thread: threading.Thread | None = None
        self.maintenance_worker_thread: threading.Thread | None = None
        self.shutdown_event = threading.Event()

        self._workers_started = start_workers

        if start_workers:
            self._start_worker()
            self._start_maintenance_worker()

            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGINT, self._shutdown_handler)
                signal.signal(signal.SIGTERM, self._shutdown_handler)
                logger.info("Signal handlers registered for graceful shutdown")

    def _start_worker(self):
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()
        logger.info("Lakehouse queue worker started")

    def _start_maintenance_worker(self):
        self.maintenance_worker_thread = threading.Thread(target=self._process_maintenance_queue, daemon=True)
        self.maintenance_worker_thread.start()
        logger.info("Lakehouse maintenance worker started")

    def _process_maintenance_queue(self):
        while not self.shutdown_event.is_set():
            try:
                task = self.maintenance_queue.get(timeout=1.0)
                if task is None:
                    break

                task_type, table_name, retention_hours = task

                try:
                    if task_type == "optimize":
                        self._optimize_table(table_name)
                    elif task_type == "vacuum":
                        self._vacuum_table(table_name, retention_hours)
                except Exception as e:
                    logger.error(f"Maintenance task failed ({task_type}): {e}", exc_info=True)
                finally:
                    self.maintenance_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Maintenance worker error: {e}", exc_info=True)

    def _process_queue(self):
        # Drain until the shutdown sentinel (#166): batches queued before
        # shutdown() are still written, not abandoned when shutdown_event is set.
        while True:
            try:
                task = self.write_queue.get(timeout=1.0)
                if task is None:
                    self.write_queue.task_done()
                    break

                table_name, data, mode = task

                try:
                    self._write_with_retry(table_name, data, mode)
                finally:
                    # Acked only after the batch is written or durably spilled (#225).
                    self.write_queue.task_done()
                    if DELTA_WRITE_QUEUE_DEPTH is not None:
                        DELTA_WRITE_QUEUE_DEPTH.set(self.write_queue.qsize())

            except queue.Empty:
                if self.shutdown_event.is_set():
                    break  # sentinel was consumed by _spill_queued_batches
                continue
            except Exception as e:
                logger.error(f"Queue worker error: {e}", exc_info=True)

    def _spill_queued_batches(self, reason: str) -> int:
        """Durably spill every batch still in the write queue (#166). Returns batches spilled."""
        spilled = 0
        while True:
            try:
                task = self.write_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if task is not None:
                    table_name, data, mode = task
                    self._spill_batch(table_name, data, mode, reason=reason)
                    spilled += 1
            finally:
                self.write_queue.task_done()
        if DELTA_WRITE_QUEUE_DEPTH is not None:
            DELTA_WRITE_QUEUE_DEPTH.set(self.write_queue.qsize())
        return spilled

    def _handle_writer_exception(self, e: Exception, table_name: str):
        logger.error(f"Write failed for {table_name}: {e}", exc_info=True)

    def _write_with_retry(
        self,
        table_name: str,
        data: list[dict[str, Any]],
        mode: Literal["append", "overwrite", "error", "ignore"],
    ) -> bool:
        """Async-path write: retry with backoff, then spill to disk (#225).

        Returns True if written. A batch is never dropped without a record:
        once retries are exhausted it is written to ``_write_spill/`` as JSONL
        (replay with ``replay_spilled_writes()``).
        """
        for attempt in range(1, self.write_retries + 1):
            if self._write_sync(table_name, data, mode):
                return True
            if DELTA_WRITE_FAILURES is not None:
                DELTA_WRITE_FAILURES.labels(table=table_name, outcome="retry").inc()
            if attempt < self.write_retries:
                delay = min(self.write_retry_backoff * (2 ** (attempt - 1)), 30.0)
                logger.warning(
                    f"Write to {table_name} failed (attempt {attempt}/{self.write_retries}); retrying in {delay:.2f}s"
                )
                if self.shutdown_event.wait(delay):
                    break  # shutting down: spill now rather than sleep
        self._spill_batch(table_name, data, mode, reason=f"write failed after {self.write_retries} attempts")
        return False

    def _spill_batch(
        self,
        table_name: str,
        data: list[dict[str, Any]],
        mode: str,
        reason: str,
    ) -> Path | None:
        """Durably persist a batch that could not be written (fsync'd JSONL)."""
        import json
        import uuid

        try:
            target_dir = self.spill_path / table_name
            target_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
            path = target_dir / f"{stamp}-{uuid.uuid4().hex[:8]}.jsonl"
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"_spill_meta": {"table": table_name, "mode": mode, "reason": reason}}) + "\n")
                for row in data:
                    fh.write(json.dumps(row, default=str) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(path)
        except Exception as e:  # last line of defence: make the loss loud
            logger.critical(f"DATA LOSS: could not spill {len(data)} rows for {table_name} ({reason}): {e}")
            return None
        if DELTA_WRITE_FAILURES is not None:
            DELTA_WRITE_FAILURES.labels(table=table_name, outcome="spilled").inc()
        logger.error(f"Spilled {len(data)} rows for {table_name} to {path} ({reason})")
        return path

    def replay_spilled_writes(self, table_name: str | None = None) -> dict[str, int]:
        """Re-write spilled batches; files are deleted only after a successful write."""
        import json

        replayed = {"files": 0, "rows": 0, "failed": 0}
        if not self.spill_path.is_dir():
            return replayed
        dirs = [self.spill_path / table_name] if table_name else sorted(self.spill_path.iterdir())
        for table_dir in dirs:
            if not table_dir.is_dir():
                continue
            for path in sorted(table_dir.glob("*.jsonl")):
                lines = path.read_text(encoding="utf-8").splitlines()
                if not lines:
                    path.unlink()
                    continue
                meta = json.loads(lines[0]).get("_spill_meta", {})
                rows = [json.loads(line) for line in lines[1:] if line.strip()]
                if self._write_sync(meta.get("table", table_dir.name), rows, meta.get("mode", "append")):
                    path.unlink()
                    replayed["files"] += 1
                    replayed["rows"] += len(rows)
                else:
                    replayed["failed"] += 1
        return replayed

    def _table_lock(self, table_name: str) -> threading.Lock:
        with self._table_locks_guard:
            lock = self._table_locks.get(table_name)
            if lock is None:
                lock = self._table_locks[table_name] = threading.Lock()
            return lock

    def _write_sync(
        self,
        table_name: str,
        data: list[dict[str, Any]],
        mode: Literal["append", "overwrite", "error", "ignore"] = "append",
        schema_overwrite: bool = False,
    ) -> bool:
        """Write synchronously. Returns False if the write failed (logged, not raised)."""
        if not data:
            return True
        with self._table_lock(table_name):
            return self._write_sync_locked(table_name, data, mode, schema_overwrite=schema_overwrite)

    @staticmethod
    def _enrich_records(table_name: str, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add partition key and ingestion metadata in place (write and merge paths).

        For domain-partitioned tables, rows whose URL has no usable host are
        removed from ``data`` and returned, to be quarantined (#458) rather
        than written to a skewed domain="unknown" partition.
        """
        undomainable: list[dict[str, Any]] = []
        if _partition_columns(table_name):
            # Partition key: public-suffix-aware registrable domain (#251).
            kept: list[dict[str, Any]] = []
            for record in data:
                if record.get("domain") in (None, "", "unknown"):
                    record.pop("domain", None)
                    try:
                        domain = partition_domain(record.get("url"))
                    except Exception:
                        domain = None
                    if domain is None:
                        undomainable.append(record)
                        continue
                    record["domain"] = domain
                kept.append(record)
            data[:] = kept

        for record in data:
            if "_ingestion_time" not in record:
                record["_ingestion_time"] = datetime.now(UTC).isoformat()
            if "_stage" not in record:
                record["_stage"] = table_name
        return undomainable

    def _quarantine_undomainable(self, table_name: str, rows: list[dict[str, Any]]) -> bool:
        """Count, log and park rows with no usable host in DOMAIN_QUARANTINE_TABLE (#458).

        Returns True when the rows are safely recorded (or there were none).
        """
        import json as _json

        if not rows:
            return True
        if DELTA_UNKNOWN_DOMAIN is not None:
            DELTA_UNKNOWN_DOMAIN.labels(table=table_name).inc(len(rows))
        sample = [str(r.get("url")) for r in rows[:5]]
        logger.warning(
            f"[DOMAIN] {len(rows)} row(s) for {table_name} have no usable host; "
            f"quarantined to {DOMAIN_QUARANTINE_TABLE}. e.g. URLs: {sample}"
        )
        now = datetime.now(UTC).isoformat()
        quarantine = [
            {
                "source_table": table_name,
                "url": str(r.get("url") or ""),
                "reason": "no_usable_host",
                "row_json": _json.dumps(r, default=str)[:10000],
                "quarantined_at": now,
            }
            for r in rows
        ]
        try:
            ok = bool(self._write_sync(DOMAIN_QUARANTINE_TABLE, quarantine, "append"))
        except Exception as e:
            logger.error(f"[DOMAIN] Failed to write {len(quarantine)} quarantine rows: {e}")
            return False
        if not ok:
            logger.error(f"[DOMAIN] Failed to write {len(quarantine)} quarantine rows")
        return ok

    def repair_unknown_domains(self, table_name: str, apply: bool = False) -> dict[str, int]:
        """Re-derive the partition key for rows already written as domain="unknown" (#458).

        Dry run by default: returns ``{"rows", "repairable", "quarantine"}``.
        With ``apply=True`` repairable rows are appended under their real
        domain, the rest go to DOMAIN_QUARANTINE_TABLE, then the "unknown"
        partition is deleted. The append happens before the delete, so a crash
        in between can duplicate rows but never loses them.
        """
        if table_name not in PARTITIONED_TABLES:
            raise ValueError(f"{table_name} is not domain-partitioned")
        table_path = self.get_table_path(table_name)
        if not (table_path / "_delta_log").exists():
            return {"rows": 0, "repairable": 0, "quarantine": 0}
        dt = DeltaTable(str(table_path))
        rows = dt.to_pyarrow_table(partitions=[("domain", "=", "unknown")]).to_pylist()
        repaired, bad = [], []
        for row in rows:
            domain = partition_domain(row.get("url"))
            if domain is None:
                bad.append(row)
            else:
                repaired.append({**row, "domain": domain})
        report = {"rows": len(rows), "repairable": len(repaired), "quarantine": len(bad)}
        if apply and rows:
            if repaired and not self._write_sync(table_name, repaired, "append"):
                raise RuntimeError(f"repair append to {table_name} failed; unknown partition left intact")
            if not self._quarantine_undomainable(table_name, bad):
                raise RuntimeError(f"quarantine write for {table_name} failed; unknown partition left intact")
            DeltaTable(str(table_path)).delete("domain = 'unknown'")
            logger.warning(f"[DOMAIN] repaired {table_name}: {report}")
        return report

    def _write_sync_locked(
        self,
        table_name: str,
        data: list[dict[str, Any]],
        mode: Literal["append", "overwrite", "error", "ignore"],
        schema_overwrite: bool = False,
    ) -> bool:

        table_path = self.tables.get(table_name)
        if not table_path:
            table_path = self.base_path / table_name
            table_path.mkdir(parents=True, exist_ok=True)
            self.tables[table_name] = table_path
            logger.info(f"Dynamically created new table path for: {table_name}")

        undomainable = self._enrich_records(table_name, data)
        if undomainable:
            self._quarantine_undomainable(table_name, undomainable)
            if not data:
                return True  # handled: every row is recorded in quarantine

        try:
            import time

            import pyarrow as pa  # noqa: F401  (lazy import: loads pyarrow on first write)
            from deltalake import WriterProperties, write_deltalake
            from deltalake.exceptions import CommitFailedError, DeltaError

            # #226/#229: the authoritative schema is the table's own, read from
            # _delta_log on every write - not whatever this process happened to
            # infer from its first batch. Policy is additive-only: existing
            # columns keep their types (rows are cast, failures quarantined),
            # new columns are appended via schema_mode="merge", and required
            # (non-nullable) columns are never null-filled.
            # #509: overwrite replaces ROWS, not the schema. It goes through the
            # same cast/evolve path as append and commits with schema_mode="merge",
            # so columns evolved by other writers survive (null in the new rows).
            # Replacing the schema itself requires schema_overwrite=True.
            if schema_overwrite and mode != "overwrite":
                raise ValueError("schema_overwrite=True requires mode='overwrite'")
            keep_schema = mode in ("append", "overwrite") and not schema_overwrite
            table_schema = self._table_schema(table_path) if keep_schema else None
            if table_schema is None:
                table = infer_table(data)
            else:
                cast_table, kept, failures = cast_rows_to_schema(
                    data, table_schema, getattr(self, "cast_mode", "strict")
                )
                if failures:
                    self._record_cast_failures(table_name, failures)
                if cast_table is None:
                    logger.error(
                        f"[CAST] All {len(data)} rows for {table_name} failed to cast; "
                        f"quarantined to {CAST_QUARANTINE_TABLE}"
                    )
                    return True  # handled: every row is recorded in quarantine
                data = kept
                table, added = evolve_table(cast_table, data, set(table_schema.names))
                if added:
                    if DELTA_SCHEMA_EVOLUTIONS is not None:
                        DELTA_SCHEMA_EVOLUTIONS.labels(table=table_name).inc(len(added))
                    logger.warning(f"[SCHEMA EVOLUTION] {table_name}: adding columns {added}")
            self.schema_cache[table_name] = table.schema  # informational only

            partition_by = _partition_columns(table_name)

            writer_props = WriterProperties(compression="ZSTD")

            max_attempts = 5
            for attempt in range(1, max_attempts + 1):
                try:
                    write_deltalake(
                        str(table_path),
                        table,
                        mode=mode,
                        schema_mode="overwrite" if schema_overwrite else "merge",
                        writer_properties=writer_props,
                        partition_by=partition_by,
                        # Applied when this write creates the table (#274).
                        configuration={CHECKPOINT_INTERVAL_PROPERTY: str(self.checkpoint_interval)},
                    )
                    break
                except (CommitFailedError, DeltaError) as commit_error:
                    # CommitFailedError: lost an optimistic-concurrency race.
                    # "version N already exists": another process committed
                    # the same version first (e.g. both creating the table).
                    is_conflict = isinstance(commit_error, CommitFailedError) or (
                        "already exists" in str(commit_error)
                    )
                    if not is_conflict or attempt == max_attempts:
                        raise
                    # Another writer committed a newer version between our
                    # read and this commit attempt (concurrent writers to
                    # the same table race on Delta Lake's optimistic
                    # concurrency control) - back off and retry against the
                    # now-current table state.
                    logger.warning(
                        f"Commit conflict writing {table_name} "
                        f"(attempt {attempt}/{max_attempts}), retrying"
                    )
                    time.sleep(0.05 * attempt)

            logger.info(f" Wrote {len(data)} records to {table_name}")
        except Exception as e:
            self._handle_writer_exception(e, table_name)
            return False

        self._sync_checkpoint_interval(table_name, table_path)

        if table_name in ["stage1_discovery", "stage2_page_analysis"] and len(data) >= 1000:
            self.maintenance_queue.put(("optimize", table_name, 0))
        return True

    def _table_schema(self, table_path: Path) -> Any:
        """Current Arrow schema from the table's _delta_log, or None if no table yet."""
        if not (table_path / "_delta_log").exists():
            return None
        return pa.schema(DeltaTable(str(table_path)).schema().to_arrow())

    def _record_cast_failures(self, table_name: str, failures: list[dict[str, Any]]) -> None:
        """Count, log and quarantine rows/values that failed to cast (#818)."""
        import json as _json

        for failure in failures:
            if DELTA_CAST_FAILURES is not None:
                DELTA_CAST_FAILURES.labels(table=table_name, column=failure["column"]).inc()
        urls = sorted({str(f["row"].get("url", "")) for f in failures if f["row"].get("url")})[:5]
        logger.warning(
            f"[CAST] {len(failures)} value(s) failed to cast for {table_name} "
            f"(mode={getattr(self, 'cast_mode', 'strict')}); e.g. URLs: {urls}"
        )
        if table_name == CAST_QUARANTINE_TABLE:
            return  # never recurse
        now = datetime.now(UTC).isoformat()
        rows = [
            {
                "source_table": table_name,
                "column": str(f["column"]),
                "value": repr(f["value"])[:500],
                "error": str(f["error"])[:500],
                "url": str(f["row"].get("url") or ""),
                "row_json": _json.dumps(f["row"], default=str)[:10000],
                "cast_mode": str(getattr(self, "cast_mode", "strict")),
                "quarantined_at": now,
            }
            for f in failures
        ]
        try:
            self._write_sync(CAST_QUARANTINE_TABLE, rows, "append")
        except Exception as e:
            logger.error(f"[CAST] Failed to write {len(rows)} quarantine rows: {e}")

    def write(
        self,
        table_name: str,
        data: list[dict[str, Any]],
        mode: Literal["append", "overwrite", "error", "ignore"] = "append",
        async_write: bool = True,
        schema_overwrite: bool = False,
    ):
        """Write data to a Delta table, optionally via the background queue.

        Returns False when the batch was not written: a failed sync write, or an
        async write that found the queue full for ``queue_put_timeout`` seconds
        and was spilled to ``_write_spill/`` instead of blocking forever (#167).

        ``mode="overwrite"`` replaces the rows but keeps the table schema
        (additive evolution only, #509). Pass ``schema_overwrite=True`` to
        deliberately replace the schema as well. That destructive write is
        always synchronous, logged, and counted.
        """
        if schema_overwrite:
            if mode != "overwrite":
                raise ValueError("schema_overwrite=True requires mode='overwrite'")
            logger.warning(f"[SCHEMA OVERWRITE] {table_name}: replacing table schema (explicit schema_overwrite=True)")
            if DELTA_SCHEMA_OVERWRITES is not None:
                DELTA_SCHEMA_OVERWRITES.labels(table=table_name).inc()
            return self._write_sync(table_name, data, mode, schema_overwrite=True)
        if async_write and self.shutdown_event.is_set():
            # Shutting down: the worker may already be gone, so a queued batch
            # could sit unread. Write it synchronously instead (#166).
            logger.debug(f"Shutdown in progress; writing {len(data)} rows for {table_name} synchronously")
            return self._write_sync(table_name, data, mode)
        if async_write:
            try:
                self.write_queue.put((table_name, data, mode), timeout=self.queue_put_timeout)
            except queue.Full:
                if DELTA_WRITE_QUEUE_FULL is not None:
                    DELTA_WRITE_QUEUE_FULL.labels(table=table_name).inc()
                logger.error(
                    f"Delta write queue full for {self.queue_put_timeout}s "
                    f"({self.write_queue.maxsize} batches); spilling {len(data)} rows for {table_name}"
                )
                self._spill_batch(table_name, data, mode, reason="write queue full")
                return False
            if DELTA_WRITE_QUEUE_DEPTH is not None:
                DELTA_WRITE_QUEUE_DEPTH.set(self.write_queue.qsize())
            logger.debug(f"Queued {len(data)} records for {table_name}")
            return True
        return self._write_sync(table_name, data, mode)

    def read(
        self,
        table_name: str,
        filters: Any = None,
        columns: list[str] | None = None,
        version: int | None = None,
    ) -> list[dict]:
        from deltalake import DeltaTable

        table_path = self.get_table_path(table_name)

        if not (table_path / "_delta_log").exists():
            logger.warning(f"No data found in {table_name}")
            return []

        table = DeltaTable(str(table_path), version=version)
        pa_table = table.to_pyarrow_table(filters=filters, columns=columns)
        rows: list[dict] = pa_table.to_pylist()
        return rows

    def count(self, table_name: str) -> int:
        from deltalake import DeltaTable

        table_path = self.get_table_path(table_name)

        if not (table_path / "_delta_log").exists():
            return 0

        # #372: never materialize rows to count them. The Delta log's add actions
        # carry per-file num_records, so the exact count costs no data I/O.
        table = DeltaTable(str(table_path))
        total = metadata_row_count(table)
        if total is not None:
            return total
        # Some files lack stats: count from parquet footers (still no data pages).
        return int(table.to_pyarrow_dataset().count_rows())

    def _optimize_table(self, table_name: str):
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path or not (table_path / "_delta_log").exists():
            return

        try:
            logger.info(f"Optimizing {table_name} with compaction...")
            try:
                self._maintenance_commit(table_name, "compact", lambda dt: dt.optimize.compact())
            except Exception as e:
                self._optimize_skipped(table_name, _maintenance_failure_reason("compact", e), f"compaction failed: {e}")

            dt = DeltaTable(str(table_path))
            z_order_columns = self.z_order_columns.get(table_name)
            if z_order_columns:
                # Validate against the table schema before calling z_order (#272).
                schema_fields = {field.name for field in dt.schema().fields}
                missing = [col for col in z_order_columns if col not in schema_fields]
                if missing:
                    self._optimize_skipped(
                        table_name,
                        "zorder_missing_columns",
                        f"Z-order skipped: columns {missing} not in table schema "
                        f"(set delta_lake.z_order_columns.{table_name} to columns the writers always produce)",
                    )
                else:
                    logger.info(f"Z-ordering {table_name} by {', '.join(z_order_columns)}...")
                    try:
                        self._maintenance_commit(
                            table_name, "z_order", lambda fresh: fresh.optimize.z_order(z_order_columns)
                        )
                    except Exception as e:
                        self._optimize_skipped(table_name, _maintenance_failure_reason("zorder", e), f"Z-order failed: {e}")

            logger.info(f" Optimized {table_name}")

        except Exception as e:
            logger.warning(f"Optimization failed for {table_name}: {e}")

    def _maintenance_commit(self, table_name: str, operation: str, action: Any) -> None:
        """Run a maintenance commit (compact / z_order / vacuum) safely (#702).

        * In-process: holds the same per-table lock as ``_write_sync``, so
          maintenance and this process's writers never interleave on a table.
        * Across processes/replicas: a ``CommitFailedError`` (another writer
          committed first) is retried on a FRESH snapshot with backoff, up to
          ``MAINTENANCE_MAX_ATTEMPTS``; each conflict is counted in
          ``delta_maintenance_conflicts_total``. The lock is released between
          attempts so writers are not starved. The last error is re-raised.
        """
        import time

        from deltalake import DeltaTable
        from deltalake.exceptions import CommitFailedError

        table_path = self.tables.get(table_name) or self.get_table_path(table_name)
        for attempt in range(1, MAINTENANCE_MAX_ATTEMPTS + 1):
            try:
                with self._table_lock(table_name):
                    action(DeltaTable(str(table_path)))
                return
            except CommitFailedError as e:
                if DELTA_MAINTENANCE_CONFLICTS is not None:
                    DELTA_MAINTENANCE_CONFLICTS.labels(table=table_name, operation=operation).inc()
                if attempt == MAINTENANCE_MAX_ATTEMPTS:
                    raise
                delay = MAINTENANCE_RETRY_BACKOFF * (2 ** (attempt - 1))
                logger.warning(
                    f"[MAINTENANCE] {operation} on {table_name} hit a concurrent commit "
                    f"(attempt {attempt}/{MAINTENANCE_MAX_ATTEMPTS}): {e}; retrying in {delay:.2f}s"
                )
                time.sleep(delay)

    def _optimize_skipped(self, table_name: str, reason: str, message: str) -> None:
        """Make a skipped/failed optimize step loud: warning + metric (#272)."""
        logger.warning(f"[OPTIMIZE] {table_name}: {message}")
        if DELTA_OPTIMIZE_SKIPPED is not None:
            DELTA_OPTIMIZE_SKIPPED.labels(table=table_name, reason=reason).inc()

    def _vacuum_table(
        self,
        table_name: str,
        retention_hours: int = 168,
        enforce_retention_duration: bool = True,
    ):
        """Vacuum Delta table to remove old data files.

        Args:
            table_name: Name of table to vacuum
            retention_hours: Retention period in hours (default: 168 = 7 days)
            enforce_retention_duration: If False, allows retention < 168 hours (DANGEROUS!)
        """
        table_path = self.tables.get(table_name)
        if not table_path or not (table_path / "_delta_log").exists():
            return

        try:
            logger.info(
                f"Vacuuming {table_name} (retention: {retention_hours}h, enforce={enforce_retention_duration})..."
            )
            self._maintenance_commit(
                table_name,
                "vacuum",
                lambda dt: dt.vacuum(
                    retention_hours=retention_hours,
                    enforce_retention_duration=enforce_retention_duration,
                    dry_run=False,
                ),
            )
            logger.info(f" Vacuumed {table_name}")
        except Exception as e:
            logger.warning(f"Vacuum failed for {table_name}: {e}")

    def vacuum_all_tables(self, retention_hours: int = 168):
        for table_name in self.tables.keys():
            self._vacuum_table(table_name, retention_hours)

    def _sync_checkpoint_interval(self, table_name: str, table_path: Path) -> None:
        """Set delta.checkpointInterval on tables created before it was configured (#274).

        Done once per table per process; failures only log, since delta-rs
        still checkpoints at its default interval.
        """
        if table_name in self._checkpoint_interval_synced:
            return
        self._checkpoint_interval_synced.add(table_name)
        want = str(self.checkpoint_interval)
        try:
            dt = DeltaTable(str(table_path))
            if dt.metadata().configuration.get(CHECKPOINT_INTERVAL_PROPERTY) != want:
                dt.alter.set_table_properties({CHECKPOINT_INTERVAL_PROPERTY: want})
                logger.info(f"Set {CHECKPOINT_INTERVAL_PROPERTY}={want} on {table_name}")
        except Exception as e:
            logger.warning(f"Could not set {CHECKPOINT_INTERVAL_PROPERTY} on {table_name}: {e}")

    def create_checkpoints(self) -> dict[str, int]:
        """Write a Delta log checkpoint for every table with commits since the last one (#274).

        Returns {table_name: checkpointed_version} for tables that got a new checkpoint.
        """
        created: dict[str, int] = {}
        if not DELTA_AVAILABLE:
            return created
        for name, table_path in self.tables.items():
            if not (table_path / "_delta_log").exists():
                continue
            try:
                dt = DeltaTable(str(table_path))
                version = dt.version()
                last = last_checkpoint_version(table_path)
                if last is not None and last >= version:
                    continue
                dt.create_checkpoint()
                created[name] = version
                if DELTA_CHECKPOINTS is not None:
                    DELTA_CHECKPOINTS.labels(table=name, outcome="created").inc()
                logger.info(f" Checkpointed {name} at version {version}")
            except Exception as e:
                if DELTA_CHECKPOINTS is not None:
                    DELTA_CHECKPOINTS.labels(table=name, outcome="failed").inc()
                logger.error(f"Failed to checkpoint {name}: {e}")
        return created

    def checkpoint(self, timeout: int = 30):

        logger.info(f"Waiting for queue to finish (timeout: {timeout}s)...")

        start_time = time.time()
        # Bounded wait (#166): Queue.join() has no timeout and hung forever when
        # the worker had already exited, so a SIGTERM'd pod never finished.
        while self.write_queue.unfinished_tasks and (time.time() - start_time) < timeout:
            time.sleep(0.05)

        elapsed = time.time() - start_time
        remaining = self.write_queue.qsize()

        if remaining > 0:
            logger.warning(f"  Queue not empty after {elapsed:.1f}s: {remaining} tasks remaining (forcing shutdown)")
        else:
            logger.info(f" Queue emptied in {elapsed:.1f}s")

        logger.info("Checkpointing all Delta tables...")
        self.create_checkpoints()

    def shutdown(self, timeout: int = 15):
        if not self._workers_started:
            logger.debug("Workers were not started, skipping shutdown")
            return

        if self.shutdown_event.is_set():
            logger.debug("Already shut down, skipping")
            return

        if DELTA_MANAGER_SHUTDOWN_TOTAL:
            DELTA_MANAGER_SHUTDOWN_TOTAL.inc()

        start_time = time.time()
        logger.info(f"🛑 Shutting down LakehouseManager (timeout: {timeout}s)...")

        self.shutdown_event.set()

        try:
            self.write_queue.put(None, timeout=1)
        except queue.Full:
            logger.warning("Write queue full, worker may be blocked")

        try:
            self.maintenance_queue.put(None, timeout=1)
        except queue.Full:
            logger.warning("Maintenance queue full, worker may be blocked")

        threads_to_join = [
            (self.worker_thread, "write worker"),
            (self.maintenance_worker_thread, "maintenance worker"),
        ]

        for thread, name in threads_to_join:
            if thread and thread.is_alive():
                logger.debug(f"Waiting for {name} to finish...")
                thread.join(timeout=timeout / 2)

                if thread.is_alive():
                    logger.warning(f"  {name} did not stop in time")
                else:
                    logger.info(f" {name} stopped gracefully")

        # Whatever the worker could not write in time is spilled, never dropped (#166).
        spilled = self._spill_queued_batches(reason="shutdown before write")
        if spilled:
            logger.error(
                f"Shutdown: spilled {spilled} queued batches to {self.spill_path} "
                "(replay with replay_spilled_writes())"
            )

        self.checkpoint(timeout=min(timeout, 5))

        duration = time.time() - start_time
        if DELTA_MANAGER_SHUTDOWN_DURATION_SECONDS:
            DELTA_MANAGER_SHUTDOWN_DURATION_SECONDS.observe(duration)
        logger.info(f" LakehouseManager shutdown complete in {duration:.2f} seconds")

    def _shutdown_handler(self, signum, frame):
        signal_name = signal.Signals(signum).name
        logger.info(f"🛑 {signal_name} received, initiating graceful shutdown...")

        try:
            self.shutdown(timeout=15)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
        finally:
            logger.info(" Shutdown handler complete")
            sys.exit(0)

    def list_tables(self) -> list[dict[str, Any]]:
        """Per-table summary. File and partition counts come from the Delta log's
        live add actions, so Hive-style partition subdirectories are counted and
        tombstoned (overwritten) files are not (#600)."""
        tables_info = []

        for table_name, table_path in self.tables.items():
            info: dict[str, Any] = {
                "name": table_name,
                "path": str(table_path),
                "exists": (table_path / "_delta_log").exists(),
                "parquet_files": 0,
                "partitions": 0,
                "row_count": 0,
            }

            if info["exists"]:
                try:
                    info["parquet_files"], info["partitions"] = table_file_stats(DeltaTable(str(table_path)))
                    info["row_count"] = self.count(table_name)
                except Exception as e:
                    info["error"] = str(e)
            elif table_path.exists():
                # No log: report stray parquet anywhere under the path.
                info["parquet_files"] = sum(1 for _ in table_path.rglob("*.parquet"))

            tables_info.append(info)

        return tables_info

    def _export_settings(
        self,
        batch_size: int | None,
        max_rows_per_file: int | None,
        max_bytes_per_file: int | None,
    ) -> tuple[int, int | None, int | None]:
        config = Config.get_instance()

        def _limit(value: Any, key: str) -> int | None:
            if value is None:
                value = config.get(f"export.{key}", None)
            if value is None:
                return None
            limit = int(value)
            return limit if limit > 0 else None

        size = batch_size if batch_size is not None else config.get("export.batch_size", None)
        size = int(size) if size else EXPORT_DEFAULT_BATCH_SIZE
        if size <= 0:
            raise ValueError("export batch_size must be positive")
        return size, _limit(max_rows_per_file, "max_rows_per_file"), _limit(max_bytes_per_file, "max_bytes_per_file")

    def export(
        self,
        table_name: str,
        output_path: str,
        format: str = "csv",
        *,
        filters: Any = None,
        columns: list[str] | None = None,
        batch_size: int | None = None,
        max_rows_per_file: int | None = None,
        max_bytes_per_file: int | None = None,
    ) -> dict[str, Any]:
        """Stream a Delta table to CSV / JSON-lines / Parquet without materializing it (#373).

        Rows are scanned in record batches of ``batch_size`` and written
        incrementally, so peak memory is bounded by the batch size rather than the
        table size.  ``filters`` (DNF tuples such as ``[("crawl_date", "=", "2026-10-07")]``
        or a ``pyarrow.dataset`` expression) scope the export to partitions/rows.

        With ``max_rows_per_file`` and/or ``max_bytes_per_file`` set (arguments or
        the ``export.*`` config keys; 0/None = unlimited) the output rolls over into
        ``<stem>.part-00000<suffix>``, ``<stem>.part-00001<suffix>``, ...  The byte
        limit is checked at batch boundaries, so a file can exceed it by at most one
        batch.
        """
        import pyarrow as pa
        import pyarrow.parquet as pq
        from deltalake import DeltaTable

        if format not in EXPORT_FORMATS:
            raise ValueError(f"Unsupported format: {format}")
        batch_size, max_rows, max_bytes = self._export_settings(batch_size, max_rows_per_file, max_bytes_per_file)
        chunked = max_rows is not None or max_bytes is not None

        table_path = self.get_table_path(table_name)
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if not (table_path / "_delta_log").exists():
            logger.warning(f"No data in table: {table_name}, exporting empty file.")
            schema = pa.schema([])
            batches: Any = iter(())
        else:
            dataset = DeltaTable(str(table_path)).to_pyarrow_dataset()
            expression = filters
            if isinstance(filters, list | tuple):
                expression = pq.filters_to_expression(filters) if filters else None
            scanner = dataset.scanner(
                columns=columns,
                filter=expression,
                batch_size=batch_size,
                batch_readahead=EXPORT_BATCH_READAHEAD,
                fragment_readahead=1,
            )
            schema = scanner.projected_schema
            batches = scanner.to_batches()

        files: list[Path] = []
        sink: _ExportSink | None = None
        total_rows = 0

        def _open() -> _ExportSink:
            path = _export_part_path(out_path, len(files)) if chunked else out_path
            files.append(path)
            return _ExportSink(path, schema, format)

        try:
            for batch in batches:
                offset = 0
                while offset < batch.num_rows:
                    if sink is None:
                        sink = _open()
                    take = batch.num_rows - offset
                    if max_rows is not None:
                        take = min(take, max_rows - sink.rows)
                    sink.write(batch.slice(offset, take))
                    offset += take
                    total_rows += take
                    if (max_rows is not None and sink.rows >= max_rows) or (
                        max_bytes is not None and sink.bytes_written() >= max_bytes
                    ):
                        sink.close()
                        sink = None
            if not files:
                sink = _open()  # header-only / empty output, as before
        finally:
            if sink is not None:
                sink.close()

        logger.info(f" Exported {table_name} to {out_path} ({format}, {total_rows} rows, {len(files)} file(s))")

        size_bytes = sum(path.stat().st_size for path in files if path.exists())
        return {
            "table": table_name,
            "output": str(files[0] if chunked else out_path),
            "files": [str(path) for path in files],
            "format": format,
            "rows": total_rows,
            "columns": len(schema),
            "size_mb": size_bytes / (1024 * 1024),
        }

    def export_all(self, output_dir: str, format: str = "csv", **options: Any) -> list[dict[str, Any]]:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for table_name in self.tables.keys():
            try:
                output_path = out_dir / f"{table_name}.{format}"
                result = self.export(table_name, str(output_path), format, **options)
                results.append(result)
            except Exception as e:
                logger.warning(f"Failed to export {table_name}: {e}")
                results.append({"table": table_name, "error": str(e)})

        return results

    def get_table_schema(self, table_name: str):
        from deltalake import DeltaTable

        table_path = self.get_table_path(table_name)

        if not (table_path / "_delta_log").exists():
            raise ValueError(f"No data found in {table_name}")

        table = DeltaTable(str(table_path))
        return table.schema().to_arrow()

    def table_exists(self, table_name: str) -> bool:
        table_path = self.tables.get(table_name)
        if not table_path:
            return False
        return (table_path / "_delta_log").exists()

    def delete_table(
        self,
        table_name: str,
        *,
        allow_destructive: bool = False,
        hard: bool = False,
        reason: str = "",
    ) -> Path | None:
        """Remove a table, guarded (#614).

        Denied unless ``allow_destructive=True``. By default the table directory
        (data plus the whole ``_delta_log``, so all time travel) is moved to
        ``<base>/_trash/<table>-<UTC stamp>/`` and can be brought back with
        ``restore_table``. ``hard=True`` deletes irreversibly and additionally
        requires ``DELTA_ALLOW_HARD_DELETE=1`` in the environment, so production
        (which never sets it) can't lose a table to one mistaken call. Every
        delete is logged and appended to ``<base>/_audit/table_deletes.jsonl``.
        To empty a table but keep its history, use ``truncate_table``.

        Returns the trash path for a soft delete, else None.
        """
        if not allow_destructive:
            raise PermissionError(
                f"delete_table({table_name!r}) refused: pass allow_destructive=True "
                "(or use truncate_table to empty it while keeping history)"
            )
        if hard and os.getenv("DELTA_ALLOW_HARD_DELETE") != "1":
            raise PermissionError(
                f"hard delete of {table_name!r} refused: set DELTA_ALLOW_HARD_DELETE=1 "
                "(irreversible: destroys all Delta history)"
            )
        table_path = self.tables.get(table_name)
        if not table_path or not table_path.exists():
            return None

        import shutil

        trash: Path | None = None
        if hard:
            shutil.rmtree(table_path)
        else:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            trash = self.base_path / TRASH_DIR / f"{table_name}-{stamp}"
            trash.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(table_path), str(trash))
        self._audit_table_delete(table_name, table_path, "hard" if hard else "soft", trash, reason)
        logger.warning(
            f"[TABLE DELETE] {table_name}: {'hard-deleted' if hard else f'moved to {trash}'}"
            + (f" ({reason})" if reason else "")
        )
        return trash

    def restore_table(self, table_name: str, trash_path: str | Path | None = None) -> Path:
        """Move a soft-deleted table back from ``_trash`` (latest copy by default)."""
        import shutil

        table_path = self.get_table_path(table_name)
        if (table_path / "_delta_log").exists():
            raise FileExistsError(f"{table_name} already exists at {table_path}; not overwriting")
        if trash_path is None:
            candidates = sorted((self.base_path / TRASH_DIR).glob(f"{table_name}-*"))
            if not candidates:
                raise FileNotFoundError(f"no trashed copy of {table_name}")
            trash_path = candidates[-1]
        if table_path.exists():
            table_path.rmdir()  # only an empty placeholder can be here (no _delta_log)
        shutil.move(str(trash_path), str(table_path))
        self._audit_table_delete(table_name, table_path, "restore", Path(trash_path), "")
        logger.warning(f"[TABLE RESTORE] {table_name} restored from {trash_path}")
        return table_path

    def truncate_table(self, table_name: str) -> bool:
        """Delete every row with a Delta DELETE commit. Schema and time travel are
        kept (earlier versions stay readable until vacuumed)."""
        table_path = self.get_table_path(table_name)
        if not (table_path / "_delta_log").exists():
            return True
        with self._table_lock(table_name):
            DeltaTable(str(table_path)).delete()
        logger.info(f"Truncated table: {table_name} (Delta DELETE; history kept)")
        return True

    def _audit_table_delete(
        self, table_name: str, table_path: Path, action: str, trash: Path | None, reason: str
    ) -> None:
        import getpass
        import json

        record = {
            "at": datetime.now(UTC).isoformat(),
            "table": table_name,
            "path": str(table_path),
            "action": action,
            "trash": str(trash) if trash else None,
            "reason": reason,
            "user": getpass.getuser(),
            "pid": os.getpid(),
        }
        try:
            audit = self.base_path / AUDIT_DIR / "table_deletes.jsonl"
            audit.parent.mkdir(parents=True, exist_ok=True)
            with open(audit, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except Exception as e:  # the log line above/below still records it
            logger.error(f"[TABLE DELETE] audit write failed for {table_name}: {e}")

    def merge_into(
        self,
        table_name: str,
        updates_data: list[dict[str, Any]],
        merge_key: str | list[str],
        update_columns: list[str],
    ) -> int:
        """
        Upsert rows via MERGE operation on key(s).

        Performs idempotent upserts: if url_hash exists, update specified columns;
        otherwise insert new row. No-op on empty list.

        Args:
            table_name: Name of target table
            updates_data: List of dictionaries to upsert
            merge_key: Column name(s) to match on (e.g., "url_hash" or ["url_hash", "type"])
            update_columns: Columns to update on match (e.g., ["url", "discovered_at"])

        Returns:
            Number of rows updated + inserted, or -1 on failure

        Note:
            Uses a Delta MERGE committed with optimistic concurrency and retried
            on conflict, so concurrent callers never lose each other's rows
            (#169). On failure nothing is committed and -1 is returned.

        Examples:
            >>> lakehouse.merge_into(
            ...     "seed_urls",
            ...     [{"url": "https://example.com", "url_hash": "abc123", "discovered_at": "..."}],
            ...     merge_key="url_hash",
            ...     update_columns=["url", "discovered_at"]
            ... )
        """
        if not updates_data:
            logger.debug(f"[merge_into] No data to merge for {table_name}")
            return 0

        # #169: a real Delta MERGE, committed with optimistic concurrency and
        # retried on conflict. The old read-all/merge-in-memory/overwrite path
        # let concurrent writers clobber each other (last writer won).
        from deltalake.exceptions import CommitFailedError, DeltaError

        merge_keys = [merge_key] if isinstance(merge_key, str) else list(merge_key)
        rows = _dedupe_by_key(updates_data, merge_keys)
        # Same partition key / metadata as write(), so merged and appended rows
        # look alike (#311: stage2_page_analysis is now upserted by url_hash).
        undomainable = self._enrich_records(table_name, rows)
        if undomainable:
            self._quarantine_undomainable(table_name, undomainable)
            if not rows:
                return 0
        try:
            table_path = self.get_table_path(table_name)
        except ValueError:  # unregistered table: create it, as write() does
            table_path = self.base_path / table_name
            table_path.mkdir(parents=True, exist_ok=True)
            self.tables[table_name] = table_path

        failure: Exception | None = None
        for attempt in range(1, MERGE_MAX_ATTEMPTS + 1):
            try:
                with self._table_lock(table_name):
                    if not (table_path / "_delta_log").exists() and self._create_from_rows(
                        table_path, rows, _partition_columns(table_name)
                    ):
                        logger.info(f"[merge_into] {table_name}: created with {len(rows)} rows")
                        return len(rows)
                    return self._merge_rows(table_name, table_path, rows, merge_keys, update_columns)
            except (CommitFailedError, DeltaError) as e:
                conflict = isinstance(e, CommitFailedError) or "already exists" in str(e)
                if conflict and attempt < MERGE_MAX_ATTEMPTS:
                    logger.warning(
                        f"[merge_into] Commit conflict on {table_name} "
                        f"(attempt {attempt}/{MERGE_MAX_ATTEMPTS}), retrying"
                    )
                    time.sleep(MERGE_RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))
                    continue
                failure = e
            except Exception as e:
                failure = e
            break

        # No overwrite/append fallback: either would lose concurrent updates or
        # duplicate keys. Nothing was committed, so callers may simply retry.
        if DELTA_MERGE_FAILURES is not None:
            DELTA_MERGE_FAILURES.labels(table=table_name).inc()
        logger.error(f"[merge_into] Failed for {table_name}: {failure}", exc_info=True)
        return -1

    def _create_from_rows(
        self, table_path: Path, rows: list[dict[str, Any]], partition_by: list[str] | None = None
    ) -> bool:
        """Create the table from ``rows``; False if another writer created it first."""
        try:
            write_deltalake(
                str(table_path),
                infer_table(rows),
                mode="error",
                partition_by=partition_by,
                configuration={CHECKPOINT_INTERVAL_PROPERTY: str(self.checkpoint_interval)},
            )
            return True
        except Exception as e:
            if (table_path / "_delta_log").exists():
                logger.debug(f"[merge_into] {table_path.name} created concurrently ({e}); merging instead")
                return False
            raise

    def _merge_rows(
        self,
        table_name: str,
        table_path: Path,
        rows: list[dict[str, Any]],
        merge_keys: list[str],
        update_columns: list[str],
    ) -> int:
        target = DeltaTable(str(table_path))
        source = _source_table(rows, pa.schema(target.schema().to_arrow()))
        predicate = " AND ".join(f"target.{k} = source.{k}" for k in merge_keys)
        updates = {c: f"source.{c}" for c in update_columns if c in source.column_names}
        merger = target.merge(
            source=source,
            predicate=predicate,
            source_alias="source",
            target_alias="target",
            merge_schema=True,
        )
        if updates:
            merger = merger.when_matched_update(updates=updates)
        metrics = merger.when_not_matched_insert_all().execute()
        updated = int(metrics.get("num_target_rows_updated", 0) or 0)
        inserted = int(metrics.get("num_target_rows_inserted", 0) or 0)
        logger.info(f"[merge_into] {table_name}: {updated} updated, {inserted} inserted")
        return updated + inserted

    def get_table_history(self, table_name: str) -> list[dict]:
        from deltalake import DeltaTable

        table_path = self.get_table_path(table_name)

        if not (table_path / "_delta_log").exists():
            return []

        table = DeltaTable(str(table_path))
        return table.history()

    # =====================================================================================
    # =====================================================================================

    def append_to_table(self, table_name: str, records: list[dict[str, Any]]) -> None:
        if not records:
            logger.debug(f"[append_to_table] No records to append for {table_name}")
            return

        self.write(table_name, records, mode="append", async_write=True)
        logger.debug(f"[append_to_table] Appended {len(records)} records to {table_name}")

    def get_table_size(self, table_name: str) -> int:
        """Total bytes of all files under the table directory (0 if absent)."""
        table_path = self.get_table_path(table_name)
        if not table_path.exists():
            return 0
        return sum(p.stat().st_size for p in table_path.rglob("*") if p.is_file())

    def read_table(self, table_name: str, **kwargs) -> list[dict]:
        return self.read(table_name, **kwargs)

    def get_table_path(self, table_name: str) -> Path:
        table_path = self.tables.get(table_name)
        if not table_path:
            # Not registered on this instance yet - a separate
            # LakehouseManager/DeltaHelper instance pointed at the same
            # base_path may have already written this table to disk.
            # self.tables is per-instance in-memory state, not derived
            # from the filesystem, so auto-discover before giving up.
            candidate_path = self.base_path / table_name
            if (candidate_path / "_delta_log").exists():
                self.tables[table_name] = candidate_path
                table_path = candidate_path
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")
        return table_path

    def __enter__(self):
        if DELTA_MANAGER_CONTEXT_ENTER_TOTAL:
            DELTA_MANAGER_CONTEXT_ENTER_TOTAL.inc()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if DELTA_MANAGER_CONTEXT_EXIT_TOTAL:
            DELTA_MANAGER_CONTEXT_EXIT_TOTAL.inc()
        if getattr(self, "_workers_started", False):
            logger.info("Context exit: Shutting down Lakehouse manager.")
            self.shutdown(timeout=5)
        return False

    _instance: "LakehouseManager | None" = None

    @classmethod
    def get_instance(cls, base_path: str | None = None, start_workers: bool = True) -> "LakehouseManager":
        """Process-wide manager; ``get_delta()`` delegates here too (#359)."""
        if cls._instance is None:
            cls._instance = cls(base_path=base_path, start_workers=start_workers)
        elif base_path is not None and Path(base_path).resolve() != Path(cls._instance.base_path).resolve():
            logger.warning(
                f"LakehouseManager.get_instance(base_path={base_path!r}) ignored: the singleton "
                f"already uses {cls._instance.base_path}"
            )
        return cls._instance

    @classmethod
    def reset_instance(cls):
        if cls._instance:
            cls._instance.shutdown()
        cls._instance = None

# =====================================================================================
# =====================================================================================

class InMemoryBackend:
    """Ephemeral test/demo backend.

    Limits (#484): time travel keeps only the last ``history_depth`` versions per
    table (kwarg, else ``delta_lake.memory_history_depth``, default
    ``MEMORY_HISTORY_DEPTH``). Version numbers stay absolute (0 = first write),
    so reading an evicted version raises ValueError rather than silently
    returning a different snapshot. Every retained version is a full copy of
    the table, so memory is about ``history_depth`` times the table size.
    """

    def __init__(self, history_depth: int | None = None, **kwargs):
        self.tables: dict[str, list[dict[str, Any]]] = {}
        # Retained snapshots, oldest first; _next_version gives absolute numbering.
        self.history: dict[str, deque[list[dict[str, Any]]]] = {}
        self._next_version: dict[str, int] = {}
        if history_depth is None:
            try:
                history_depth = Config.get_instance().get("delta_lake.memory_history_depth", None)
            except Exception:
                history_depth = None
        self.history_depth = max(1, int(MEMORY_HISTORY_DEPTH if history_depth is None else history_depth))
        self.base_path = Path("./data/test_delta_lake")
        self.table_paths = {
            "seed_urls": Path("./data/delta_lake/seed_urls"),
            "stage1_discovery": Path("./data/delta_lake/stage1_discovery"),
        }

    def write(
        self,
        table_name: str,
        rows: list[dict[str, Any]],
        mode: str = "append",
        **kwargs,
    ):
        """Write rows to an in-memory table."""
        if not rows:
            return

        if table_name not in self.tables:
            self.tables[table_name] = []

        if mode == "append":
            self.tables[table_name].extend(rows)
        elif mode == "overwrite":
            self.tables[table_name] = rows
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        if table_name not in self.history:
            self.history[table_name] = deque(maxlen=self.history_depth)
        self.history[table_name].append(list(self.tables[table_name]))
        self._next_version[table_name] = self._next_version.get(table_name, 0) + 1

    def _oldest_version(self, table_name: str) -> int:
        return self._next_version.get(table_name, 0) - len(self.history.get(table_name, ()))

    def _get_version(self, table_name: str, version: int | None = None) -> list[dict[str, Any]]:
        if version is None:
            return self.tables.get(table_name, [])
        oldest = self._oldest_version(table_name)
        if table_name not in self.history or not oldest <= version < self._next_version[table_name]:
            raise ValueError(
                f"Version {version} not available for table {table_name} "
                f"(retained: {oldest}..{self._next_version.get(table_name, 0) - 1}, "
                f"history_depth={self.history_depth})"
            )
        return self.history[table_name][version - oldest]

    def read(
        self,
        table_name: str,
        filter: Any = None,
        columns: list[str] | None = None,
        version: int | None = None,
    ) -> list[dict]:
        """Read rows from in-memory table, with optional column selection."""
        if table_name not in self.tables:
            raise ValueError(f"Unknown table: {table_name}")

        data = self._get_version(table_name, version)

        if filter:
            key, value = filter.split("=")
            key = key.strip()
            value = value.strip().strip("'")
            data = [row for row in data if row.get(key) == value]

        if columns:
            return [{col: row.get(col) for col in columns} for row in data]
        return data

    def list_tables(self) -> list[str]:
        return list(self.tables.keys())

    def table_exists(self, name: str) -> bool:
        return name in self.tables

    def truncate_table(self, name: str) -> bool:
        if name in self.tables:
            self.tables[name] = []
        return True

    def delete_table(self, name: str, *, allow_destructive: bool = False, **kwargs):
        if not allow_destructive:
            raise PermissionError(f"delete_table({name!r}) refused: pass allow_destructive=True")
        if name in self.tables:
            del self.tables[name]
        self.history.pop(name, None)  # snapshots of a deleted table are not kept (#484)
        self._next_version.pop(name, None)

    def get_table_schema(self, name: str):
        if not self.table_exists(name) or not self.tables[name]:
            import pyarrow as pa

            return pa.schema([])

        import pyarrow as pa

        first_record = self.tables[name][0]
        fields = []
        for key, value in first_record.items():
            if isinstance(value, bool):
                field_type = pa.bool_()
            elif isinstance(value, int):
                field_type = pa.int64()
            elif isinstance(value, float):
                field_type = pa.float64()
            elif isinstance(value, str):
                field_type = pa.string()
            else:
                field_type = pa.string()
            fields.append(pa.field(key, field_type))

        return pa.schema(fields)

    def get_table_history(self, name: str) -> list[dict]:
        if not self.table_exists(name):
            return []

        oldest = self._oldest_version(name)
        return [
            {
                "version": oldest + i,
                "timestamp": datetime.now(UTC).isoformat(),
                "operation": "WRITE",
                "operationParameters": {"mode": "Append", "partitionBy": "[]"},
                "user": "test-user",
            }
            for i in range(len(self.history.get(name, ())))
        ]

    def add_to_batch(self, table: str, rows: list[dict]):
        self.write(table, rows, mode="append")

    def flush_all(self):
        pass

    def flush_batch(self, table_name: str):
        pass

    def flush_all_batches(self):
        pass

    def count(self, table_name: str) -> int:
        return len(self.tables.get(table_name, []))

    def merge_into(
        self,
        table_name: str,
        updates_data: list[dict[str, Any]],
        merge_key: str | list[str],
        update_columns: list[str],
    ) -> int:
        """
        Upsert rows via MERGE operation on key(s) (in-memory implementation).

        Performs idempotent upserts: if merge_key exists, update specified columns;
        otherwise insert new row.

        Args:
            table_name: Name of target table
            updates_data: List of dictionaries to upsert
            merge_key: Column name(s) to match on (e.g., "url_hash" or ["url_hash", "type"])
            update_columns: Columns to update on match (e.g., ["url", "discovered_at"])

        Returns:
            Number of rows affected

        Examples:
            >>> backend.merge_into(
            ...     "seed_urls",
            ...     [{"url": "https://example.com", "url_hash": "abc123", "discovered_at": "..."}],
            ...     merge_key="url_hash",
            ...     update_columns=["url", "discovered_at"]
            ... )
        """
        if not updates_data:
            return 0

        if table_name not in self.tables:
            self.tables[table_name] = []

        merge_keys = [merge_key] if isinstance(merge_key, str) else merge_key

        existing_data = self.tables[table_name]
        existing_index: dict[tuple, int] = {}
        for idx, row in enumerate(existing_data):
            row_key = tuple(row.get(k) for k in merge_keys)
            existing_index[row_key] = idx

        updates_count = 0
        inserts_count = 0

        for update_row in updates_data:
            row_key = tuple(update_row.get(k) for k in merge_keys)

            if row_key in existing_index:
                idx = existing_index[row_key]
                existing_row = existing_data[idx]
                for col in update_columns:
                    if col in update_row:
                        existing_row[col] = update_row[col]
                updates_count += 1
            else:
                existing_data.append(update_row)
                inserts_count += 1

        logger.debug(f"[InMemory merge_into] {table_name}: {updates_count} updated, {inserts_count} inserted")
        return updates_count + inserts_count

    def append_to_table(self, table_name: str, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        self.write(table_name, records, mode="append")

    def get_table_size(self, table_name: str) -> int:
        """Total bytes of all files under the table directory (0 if absent)."""
        table_path = self.get_table_path(table_name)
        if not table_path.exists():
            return 0
        return sum(p.stat().st_size for p in table_path.rglob("*") if p.is_file())

    def read_table(self, table_name: str, **kwargs) -> list[dict]:
        return self.read(table_name, **kwargs)

    def get_table_path(self, table_name: str) -> Path:
        if table_name in self.table_paths:
            return self.table_paths[table_name]
        return self.base_path / table_name

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

# =====================================================================================
# =====================================================================================
def get_lakehouse_manager(mode: str | None = None, **kwargs) -> LakehouseManager | InMemoryBackend:
    from_env = mode is None
    mode = mode or os.getenv("DELTA_BACKEND", "lakehouse")

    if mode == "memory":
        # #621: an env typo or leftover DELTA_BACKEND=memory used to make a
        # whole crawl run write nowhere, with only a warning. Selecting the
        # ephemeral backend from the environment now needs an explicit opt-in;
        # code that passes mode="memory" deliberately (tests, demos) is unaffected.
        if from_env and os.getenv("ALLOW_INMEMORY_DELTA") != "1":
            raise RuntimeError(
                "DELTA_BACKEND=memory selects an ephemeral lake that loses every write "
                "when the process exits. Set ALLOW_INMEMORY_DELTA=1 to use it on purpose, "
                "or DELTA_BACKEND=lakehouse for durable storage."
            )
        logger.warning(
            "  Using in-memory Lakehouse backend! "
            "All data is ephemeral and will be lost when the process exits. "
            "Set DELTA_BACKEND='lakehouse' for persistent storage."
        )
        return InMemoryBackend(**kwargs)

    if "start_workers" not in kwargs:
        kwargs["start_workers"] = True
    return LakehouseManager.get_instance(**kwargs)

@contextmanager
def lakehouse_session(mode: str | None = None, **kwargs):
    mgr = get_lakehouse_manager(mode, **kwargs)
    try:
        yield mgr
    finally:
        if hasattr(mgr, "flush_all"):
            try:
                mgr.flush_all()
            except Exception as e:
                logger.error(f"Failed to flush lakehouse session: {e}", exc_info=True)
        if isinstance(mgr, LakehouseManager):
            LakehouseManager.reset_instance()

# =====================================================================================
# =====================================================================================

DeltaLakeManager = LakehouseManager
InMemoryDeltaManager = InMemoryBackend
get_delta_manager = get_lakehouse_manager
delta_session = lakehouse_session
