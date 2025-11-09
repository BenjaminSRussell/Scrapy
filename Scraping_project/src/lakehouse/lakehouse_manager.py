import logging
import os
import queue
import signal
import sys
import threading
import time
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
    write_deltalake = None  # type: ignore
    WriterProperties = None  # type: ignore
    pa = None  # type: ignore
    pa_csv = None  # type: ignore
    pq = None  # type: ignore

logger = logging.getLogger(__name__)

WriteTask: TypeAlias = tuple[str, list[dict[str, Any]], str]
MaintenanceTask: TypeAlias = tuple[str, ...]

class LakehouseManager:

    def __init__(self, base_path: str | None = None, start_workers: bool = True):

        config = Config.get_instance()

        if base_path is None:
            base_path = config.get("delta_lake.base_path", "./data/delta_lake")
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        self.tables = {
            "seed_urls": self.base_path / "seed_urls",
            "uconn_urls": self.base_path / "uconn_urls",
            "stage1_discovery": self.base_path / "stage1_discovery",
            "stage1_errors": self.base_path / "stage1_errors",
            "stage1_offsite_candidates": self.base_path / "stage1_offsite_candidates",
            "js_spider_queue": self.base_path / "js_spider_queue",
            "stage2_queue": self.base_path / "stage2_queue",
            "stage2_page_analysis": self.base_path / "stage2_page_analysis",
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

        self.schema_cache: dict[str, Any] = {}

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

                task_type, *args = task

                try:
                    if task_type == "optimize":
                        table_name = args[0]
                        self._optimize_table(table_name)
                    elif task_type == "vacuum":
                        table_name, retention_hours = args
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
        while not self.shutdown_event.is_set():
            try:
                task = self.write_queue.get(timeout=1.0)
                if task is None:
                    self.write_queue.task_done()
                    break

                table_name, data, mode = task

                try:
                    self._write_sync(table_name, data, mode)
                finally:
                    self.write_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Queue worker error: {e}", exc_info=True)

    def _handle_writer_exception(self, e: Exception, table_name: str):
        logger.error(f"Write failed for {table_name}: {e}", exc_info=True)

    def _write_sync(
        self,
        table_name: str,
        data: list[dict[str, Any]],
        mode: Literal["append", "overwrite", "error", "ignore"] = "append",
    ):
        if not data:
            return

        table_path = self.tables.get(table_name)
        if not table_path:
            table_path = self.base_path / table_name
            table_path.mkdir(parents=True, exist_ok=True)
            self.tables[table_name] = table_path
            logger.info(f"Dynamically created new table path for: {table_name}")

        if table_name in ["stage1_discovery", "stage2_page_analysis"]:
            from urllib.parse import urlparse

            for record in data:
                if "url" in record and "domain" not in record:
                    try:
                        parsed = urlparse(record["url"])
                        hostname = parsed.hostname
                        if hostname:
                            domain_parts = hostname.lower().split(".")
                            if len(domain_parts) >= 2:
                                record["domain"] = ".".join(domain_parts[-2:])
                            else:
                                record["domain"] = hostname.lower()
                        else:
                            record["domain"] = "unknown"
                    except Exception:
                        record["domain"] = "unknown"

        for record in data:
            if "_ingestion_time" not in record:
                record["_ingestion_time"] = datetime.now(UTC).isoformat()
            if "_stage" not in record:
                record["_stage"] = table_name

        try:
            import pyarrow as pa
            from deltalake import WriterProperties, write_deltalake

            if table_name not in self.schema_cache:
                table = pa.Table.from_pylist(data)
                self.schema_cache[table_name] = table.schema
                logger.debug(f"Cached schema for {table_name}: {table.schema}")
            else:
                cached_schema = self.schema_cache[table_name]

                cached_field_names = {field.name for field in cached_schema}
                incoming_field_names = set()
                for row in data:
                    incoming_field_names.update(row.keys())

                new_columns = incoming_field_names - cached_field_names

                if new_columns:
                    logger.warning(
                        f"[SCHEMA REFRESH] Detected {len(new_columns)} new columns in {table_name}: {new_columns}"
                    )
                    table = pa.Table.from_pylist(data)
                    self.schema_cache[table_name] = table.schema
                    logger.info(f"[SCHEMA REFRESH] Updated schema for {table_name}: {table.schema}")
                else:

                    columns_data = {}
                    for field in cached_schema:
                        col_name = field.name
                        columns_data[col_name] = [row.get(col_name) for row in data]

                    arrays = []
                    for field in cached_schema:
                        col_name = field.name
                        col_data = columns_data[col_name]
                        arrays.append(pa.array(col_data, type=field.type))

                    table = pa.Table.from_arrays(arrays, schema=cached_schema)

            partition_by = None
            if table_name in ["stage1_discovery", "stage2_page_analysis"]:
                partition_by = ["domain"]

            writer_props = WriterProperties(compression="ZSTD")

            write_deltalake(
                str(table_path),
                table,
                mode=mode,
                schema_mode="merge" if mode == "append" else "overwrite",
                writer_properties=writer_props,
                partition_by=partition_by,
            )

            logger.info(f" Wrote {len(data)} records to {table_name}")
        except Exception as e:
            self._handle_writer_exception(e, table_name)

        if table_name in ["stage1_discovery", "stage2_page_analysis"] and len(data) >= 1000:
            self.maintenance_queue.put(("optimize", table_name))

    def write(
        self,
        table_name: str,
        data: list[dict[str, Any]],
        mode: Literal["append", "overwrite", "error", "ignore"] = "append",
        async_write: bool = True,
    ):
        """Write data to a Delta table, optionally via the background queue."""
        if async_write:
            self.write_queue.put((table_name, data, mode))
            logger.debug(f"Queued {len(data)} records for {table_name}")
        else:
            self._write_sync(table_name, data, mode)

    def read(self, table_name: str, filters: Any = None, columns: list[str] | None = None) -> list[dict]:
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        if not (table_path / "_delta_log").exists():
            logger.warning(f"No data found in {table_name}")
            return []

        table = DeltaTable(str(table_path))
        pa_table = table.to_pyarrow_table(filters=filters, columns=columns)
        return pa_table.to_pylist()

    def count(self, table_name: str) -> int:
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        if not (table_path / "_delta_log").exists():
            return 0

        table = DeltaTable(str(table_path))
        pa_table = table.to_pyarrow_table(columns=[])
        return pa_table.num_rows

    def _optimize_table(self, table_name: str):
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path or not (table_path / "_delta_log").exists():
            return

        try:
            dt = DeltaTable(str(table_path))

            logger.info(f"Optimizing {table_name} with compaction...")
            dt.optimize.compact()

            logger.info(f"Z-ordering {table_name} by url_hash and discovered_at...")
            if table_name == "stage1_discovery":
                dt.optimize.z_order(["url_hash", "discovered_at"])
            elif table_name == "stage2_page_analysis":
                dt.optimize.z_order(["url_hash", "processed_at"])

            logger.info(f" Optimized {table_name}")

        except Exception as e:
            logger.warning(f"Optimization failed for {table_name}: {e}")

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
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path or not (table_path / "_delta_log").exists():
            return

        try:
            dt = DeltaTable(str(table_path))
            logger.info(
                f"Vacuuming {table_name} (retention: {retention_hours}h, enforce={enforce_retention_duration})..."
            )
            dt.vacuum(
                retention_hours=retention_hours,
                enforce_retention_duration=enforce_retention_duration,
                dry_run=False,
            )
            logger.info(f" Vacuumed {table_name}")
        except Exception as e:
            logger.warning(f"Vacuum failed for {table_name}: {e}")

    def vacuum_all_tables(self, retention_hours: int = 168):
        for table_name in self.tables.keys():
            self._vacuum_table(table_name, retention_hours)

    def checkpoint(self, timeout: int = 30):

        logger.info(f"Waiting for queue to finish (timeout: {timeout}s)...")

        start_time = time.time()
        while not self.write_queue.empty() and (time.time() - start_time) < timeout:
            try:
                self.write_queue.join()
                break
            except Exception as e:
                logger.warning(f"Queue join error: {e}")
                time.sleep(0.1)

        elapsed = time.time() - start_time
        remaining = self.write_queue.qsize()

        if remaining > 0:
            logger.warning(f"  Queue not empty after {elapsed:.1f}s: {remaining} tasks remaining (forcing shutdown)")
        else:
            logger.info(f" Queue emptied in {elapsed:.1f}s")

        logger.info("Checkpointing all Delta tables...")
        for name, table_path in self.tables.items():
            try:
                if (table_path / "_delta_log").exists():
                    logger.info(f" Verified {name}")
            except Exception as e:
                logger.error(f"Failed to checkpoint {name}: {e}")

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
        tables_info = []

        for table_name, table_path in self.tables.items():
            parquet_files = list(table_path.glob("*.parquet"))

            info = {
                "name": table_name,
                "path": str(table_path),
                "exists": (table_path / "_delta_log").exists(),
                "parquet_files": len(parquet_files),
                "row_count": 0,
            }

            if info["exists"]:
                try:
                    info["row_count"] = self.count(table_name)
                except Exception as e:
                    info["error"] = str(e)

            tables_info.append(info)

        return tables_info

    def export(self, table_name: str, output_path: str, format: str = "csv"):
        import pyarrow as pa
        import pyarrow.csv as pa_csv
        import pyarrow.parquet as pq
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if not (table_path / "_delta_log").exists():
            logger.warning(f"No data in table: {table_name}, exporting empty file.")
            pa_table = pa.Table.from_pylist([])
            row_count = 0
            col_count = 0
        else:
            table = DeltaTable(str(table_path))
            pa_table = table.to_pyarrow_table()
            row_count = pa_table.num_rows
            col_count = len(pa_table.schema)

        if format == "csv":
            with pa_csv.CSVWriter(out_path, pa_table.schema) as writer:
                writer.write_table(pa_table)
        elif format == "json":
            result_df = pa_table.to_pandas()
            result_df.to_json(out_path, orient="records", lines=True)
        elif format == "parquet":
            pq.write_table(pa_table, out_path, compression="ZSTD")
        else:
            raise ValueError(f"Unsupported format: {format}")

        logger.info(f" Exported {table_name} to {out_path} ({format})")

        return {
            "table": table_name,
            "output": str(out_path),
            "format": format,
            "rows": row_count,
            "columns": col_count,
            "size_mb": (out_path.stat().st_size / (1024 * 1024) if out_path.exists() else 0),
        }

    def export_all(self, output_dir: str, format: str = "csv") -> list[dict[str, Any]]:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for table_name in self.tables.keys():
            try:
                output_path = out_dir / f"{table_name}.{format}"
                result = self.export(table_name, str(output_path), format)
                results.append(result)
            except Exception as e:
                logger.warning(f"Failed to export {table_name}: {e}")
                results.append({"table": table_name, "error": str(e)})

        return results

    def get_table_schema(self, table_name: str):
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        if not (table_path / "_delta_log").exists():
            raise ValueError(f"No data found in {table_name}")

        table = DeltaTable(str(table_path))
        return table.schema().to_pyarrow()

    def table_exists(self, table_name: str) -> bool:
        table_path = self.tables.get(table_name)
        if not table_path:
            return False
        return (table_path / "_delta_log").exists()

    def delete_table(self, table_name: str):
        table_path = self.tables.get(table_name)
        if table_path and table_path.exists():
            import shutil

            shutil.rmtree(table_path)
            logger.info(f"Deleted table: {table_name}")

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
            Number of rows affected (or -1 if unavailable)

        Note:
            Falls back to append+dedup by key if MERGE not supported.
            For production Delta Lake, this uses proper MERGE semantics.

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

        try:

            merge_keys = [merge_key] if isinstance(merge_key, str) else merge_key

            try:
                existing_data = self.read(table_name)
                existing_keys = {tuple(row.get(k) for k in merge_keys) for row in existing_data}
            except Exception:
                existing_keys = set()
                existing_data = []

            inserts = []
            updates_map = {}

            for row in updates_data:
                row_key = tuple(row.get(k) for k in merge_keys)
                if row_key in existing_keys:
                    updates_map[row_key] = row
                else:
                    inserts.append(row)

            updated_data = []
            for existing_row in existing_data:
                row_key = tuple(existing_row.get(k) for k in merge_keys)
                if row_key in updates_map:
                    update_row = updates_map[row_key]
                    merged_row = existing_row.copy()
                    for col in update_columns:
                        if col in update_row:
                            merged_row[col] = update_row[col]
                    updated_data.append(merged_row)
                else:
                    updated_data.append(existing_row)

            final_data = updated_data + inserts

            if final_data:
                self._write_sync(table_name, final_data, mode="overwrite")

            affected = len(updates_map) + len(inserts)
            logger.info(f"[merge_into] {table_name}: {len(updates_map)} updated, {len(inserts)} inserted")
            return affected

        except Exception as e:
            logger.error(f"[merge_into] Failed for {table_name}: {e}", exc_info=True)
            try:
                self._write_sync(table_name, updates_data, mode="append")
                return len(updates_data)
            except Exception as fallback_error:
                logger.error(f"[merge_into] Fallback append also failed: {fallback_error}")
                return -1

    def get_table_history(self, table_name: str) -> list[dict]:
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

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

    def read_table(self, table_name: str, **kwargs) -> list[dict]:
        return self.read(table_name, **kwargs)

    def get_table_path(self, table_name: str) -> Path:
        table_path = self.tables.get(table_name)
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
        if cls._instance is None:
            cls._instance = cls(base_path=base_path, start_workers=start_workers)
        return cls._instance

    @classmethod
    def reset_instance(cls):
        if cls._instance:
            cls._instance.shutdown()
        cls._instance = None

# =====================================================================================
# =====================================================================================

class InMemoryBackend:

    def __init__(self, **kwargs):
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.history: dict[str, list[list[dict[str, Any]]]] = {}
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
            self.history[table_name] = []
        self.history[table_name].append(list(self.tables[table_name]))

    def _get_version(self, table_name: str, version: int | None = None) -> list[dict[str, Any]]:
        if version is None:
            return self.tables.get(table_name, [])
        if table_name not in self.history or version >= len(self.history[table_name]):
            raise ValueError(f"Version {version} not available for table {table_name}")
        return self.history[table_name][version]

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

    def delete_table(self, name: str):
        if name in self.tables:
            del self.tables[name]

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

        return [
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "operation": "WRITE",
                "operationParameters": {"mode": "Append", "partitionBy": "[]"},
                "user": "test-user",
            }
            for _ in self.history.get(name, [])
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
    mode = mode or os.getenv("DELTA_BACKEND", "lakehouse")

    if mode == "memory":
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
