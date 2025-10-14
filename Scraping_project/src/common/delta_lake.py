"""Manage Delta Lake tables with a queued writer."""

import logging
import queue
import signal
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.common.config import get_config

logger = logging.getLogger(__name__)


class DeltaLakeManager:
    """Handle Delta Lake writes, reads, and lightweight maintenance."""

    def __init__(self, base_path: str | None = None, start_workers: bool = True):
        """Prepare table directories and, optionally, start queue workers."""
        # Heavy imports are lazy-loaded in methods that require them

        # Load configuration
        config = get_config()

        # Get base_path from config (defaults to ./data/delta_lake if not specified)
        if base_path is None:
            base_path = config.get('delta_lake.base_path', './data/delta_lake')
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Stage-specific tables with intelligent routing
        self.tables = {
            'seed_urls': self.base_path / 'seed_urls',
            'stage1_discovery': self.base_path / 'stage1_discovery',
            'stage1_errors': self.base_path / 'stage1_errors',
            'stage1_offsite_candidates': self.base_path / 'stage1_offsite_candidates',
            'js_spider_queue': self.base_path / 'js_spider_queue',
            'stage2_queue': self.base_path / 'stage2_queue',
            'stage2_page_analysis': self.base_path / 'stage2_page_analysis',
            'stage3_analytics': self.base_path / 'stage3_analytics',
            'stage3_summaries': self.base_path / 'stage3_summaries',
            'stage4_large_docs': self.base_path / 'stage4_large_docs',
            'stage4_summaries': self.base_path / 'stage4_summaries',
        }

        # Create table directories
        for table_path in self.tables.values():
            table_path.mkdir(parents=True, exist_ok=True)

        # Concurrent write queue with maxsize to prevent memory overload
        # This creates backpressure when spiders produce faster than we can write
        queue_maxsize = config.get('delta_lake.queue_maxsize', 1000)
        self.write_queue = queue.Queue(maxsize=queue_maxsize)

        # Schema cache to prevent schema drift
        self.schema_cache = {}

        # Maintenance queue for async optimization tasks
        self.maintenance_queue = queue.Queue()

        self.worker_thread = None
        self.maintenance_worker_thread = None
        self.shutdown_event = threading.Event()

        # Track whether workers are started (for proper cleanup)
        self._workers_started = start_workers

        # Conditionally start background workers
        if start_workers:
            self._start_worker()
            self._start_maintenance_worker()

            # Register shutdown handlers (only on main thread to prevent subprocess crashes)
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGINT, self._shutdown_handler)
                signal.signal(signal.SIGTERM, self._shutdown_handler)
                logger.info("Signal handlers registered for graceful shutdown")

    def _start_worker(self):
        """Start background worker for concurrent writes."""
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()
        logger.info("Delta Lake queue worker started")

    def _start_maintenance_worker(self):
        """Start background worker for maintenance tasks (optimization, vacuum, etc.)."""
        self.maintenance_worker_thread = threading.Thread(target=self._process_maintenance_queue, daemon=True)
        self.maintenance_worker_thread.start()
        logger.info("Delta Lake maintenance worker started")

    def _process_maintenance_queue(self):
        """Process maintenance tasks (optimization, vacuum) in background."""
        while not self.shutdown_event.is_set():
            try:
                task = self.maintenance_queue.get(timeout=1.0)
                if task is None:  # Shutdown signal
                    break

                task_type, *args = task

                try:
                    if task_type == 'optimize':
                        table_name = args[0]
                        self._optimize_table(table_name)
                    elif task_type == 'vacuum':
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
        """Process write queue in background."""
        while not self.shutdown_event.is_set():
            try:
                # Get write task with timeout
                task = self.write_queue.get(timeout=1.0)
                if task is None:  # Shutdown signal
                    self.write_queue.task_done()
                    break

                table_name, data, mode = task

                try:
                    # Perform write (exception handled internally by _write_sync)
                    self._write_sync(table_name, data, mode)
                finally:
                    # Always call task_done() to prevent deadlocks in checkpoint()
                    self.write_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Queue worker error: {e}", exc_info=True)

    def _handle_writer_exception(self, e: Exception, table_name: str):
        """Unified exception handler for all write paths."""
        logger.error(f"Write failed for {table_name}: {e}", exc_info=True)
        # Optional: Add metrics or circuit breaker logic here in the future

    def _write_sync(self, table_name: str, data: list[dict[str, Any]], mode: str = "append"):
        """Synchronous write to Delta table."""
        if not data:
            return

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        # Enhanced: Extract domain from URLs for partitioning (for discovery tables)
        if table_name in ['stage1_discovery', 'stage2_page_analysis']:
            from urllib.parse import urlparse
            for record in data:
                if 'url' in record and 'domain' not in record:
                    try:
                        parsed = urlparse(record['url'])
                        # Use hostname instead of netloc to remove port numbers
                        # This prevents fragmentation: foo.uconn.edu:8080 and foo.uconn.edu go to same partition
                        hostname = parsed.hostname
                        if hostname:
                            # Extract domain without subdomain (e.g., 'uconn.edu' from 'www.uconn.edu')
                            domain_parts = hostname.lower().split('.')
                            if len(domain_parts) >= 2:
                                record['domain'] = '.'.join(domain_parts[-2:])
                            else:
                                record['domain'] = hostname.lower()
                        else:
                            record['domain'] = 'unknown'
                    except Exception:
                        record['domain'] = 'unknown'

        # Add metadata with UTC timestamps for consistency
        for record in data:
            if '_ingestion_time' not in record:
                # Use UTC timezone-aware timestamps for consistency across all tables
                record['_ingestion_time'] = datetime.now(UTC).isoformat()
            if '_stage' not in record:
                record['_stage'] = table_name

        try:
            # Lazy-load heavy libraries on first use
            from deltalake import WriterProperties, write_deltalake
            import pyarrow as pa

            # Get or create schema for this table to prevent schema drift
            if table_name not in self.schema_cache:
                # First write: infer and cache the schema
                table = pa.Table.from_pylist(data)
                self.schema_cache[table_name] = table.schema
                logger.debug(f"Cached schema for {table_name}: {table.schema}")
            else:
                # Subsequent writes: use cached schema to enforce consistency
                table = pa.Table.from_pylist(data, schema=self.schema_cache[table_name])

            # Enhanced: Partition by domain for discovery tables
            partition_by = None
            if table_name in ['stage1_discovery', 'stage2_page_analysis']:
                partition_by = ['domain']

            # Write to Delta Lake with compression enabled via WriterProperties
            # Enable ZSTD compression to reduce disk space usage
            writer_props = WriterProperties(compression="ZSTD")

            write_deltalake(
                str(table_path),
                table,
                mode=mode,
                schema_mode="merge" if mode == "append" else "overwrite",
                writer_properties=writer_props,
                # Partition by domain
                partition_by=partition_by
            )

            logger.info(f"✅ Wrote {len(data)} records to {table_name}")
        except Exception as e:
            self._handle_writer_exception(e, table_name)

        # Enhanced: Queue optimization task instead of blocking the write path
        if table_name in ['stage1_discovery', 'stage2_page_analysis'] and len(data) >= 1000:
            self.maintenance_queue.put(('optimize', table_name))

    def write(self, table_name: str, data: list[dict[str, Any]], mode: str = "append", async_write: bool = True):
        """Write data to a Delta table, optionally via the background queue."""
        if async_write:
            # Queue for background processing
            self.write_queue.put((table_name, data, mode))
            logger.debug(f"Queued {len(data)} records for {table_name}")
        else:
            # Immediate synchronous write
            self._write_sync(table_name, data, mode)

    def read(self, table_name: str, filters: Any = None, columns: list[str] | None = None) -> list[dict]:
        """Read rows from a Delta table into a list of dictionaries."""
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        if not (table_path / "_delta_log").exists():
            logger.warning(f"No data found in {table_name}")
            return []

        table = DeltaTable(str(table_path))
        # Enable selective column reads to save memory and time
        pa_table = table.to_pyarrow_table(filters=filters, columns=columns)
        return pa_table.to_pylist()

    def count(self, table_name: str) -> int:
        """Get record count for a table using metadata (efficient, no data loading)."""
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        if not (table_path / "_delta_log").exists():
            return 0

        table = DeltaTable(str(table_path))
        # Read from metadata without loading file contents
        pa_table = table.to_pyarrow_table(columns=[])
        return pa_table.num_rows

    def _optimize_table(self, table_name: str):
        """Compact files and apply Z-ordering to improve read performance."""
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path or not (table_path / "_delta_log").exists():
            return

        try:
            dt = DeltaTable(str(table_path))

            # Perform compaction (combine small files)
            logger.info(f"Optimizing {table_name} with compaction...")
            dt.optimize.compact()

            logger.info(f"Z-ordering {table_name} by url_hash and discovered_at...")
            if table_name == 'stage1_discovery':
                dt.optimize.z_order(['url_hash', 'discovered_at'])
            elif table_name == 'stage2_page_analysis':
                dt.optimize.z_order(['url_hash', 'processed_at'])

            logger.info(f"✅ Optimized {table_name}")

        except Exception as e:
            logger.warning(f"Optimization failed for {table_name}: {e}")

    def _vacuum_table(self, table_name: str, retention_hours: int = 168, enforce_retention_duration: bool = True):
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
            logger.info(f"Vacuuming {table_name} (retention: {retention_hours}h, enforce={enforce_retention_duration})...")
            dt.vacuum(retention_hours=retention_hours, enforce_retention_duration=enforce_retention_duration, dry_run=False)
            logger.info(f"✅ Vacuumed {table_name}")
        except Exception as e:
            logger.warning(f"Vacuum failed for {table_name}: {e}")

    def vacuum_all_tables(self, retention_hours: int = 168):
        """Vacuum all tables to remove old data files.

        Args:
            retention_hours: Retention period in hours (default: 168 = 7 days)
        """
        for table_name in self.tables.keys():
            self._vacuum_table(table_name, retention_hours)

    def checkpoint(self, timeout: int = 30):
        """Wait for queue to finish and checkpoint all tables with timeout.

        Args:
            timeout: Maximum seconds to wait for queue to finish (default: 30)
        """
        import time

        logger.info(f"Waiting for queue to finish (timeout: {timeout}s)...")

        # Try to join queue with timeout
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
            logger.warning(f"⚠️  Queue not empty after {elapsed:.1f}s: {remaining} tasks remaining (forcing shutdown)")
        else:
            logger.info(f"✅ Queue emptied in {elapsed:.1f}s")

        logger.info("Checkpointing all Delta tables...")
        for name, table_path in self.tables.items():
            try:
                if (table_path / "_delta_log").exists():
                    # Skip vacuum during shutdown (too slow)
                    # table.vacuum(retention_hours=168)
                    logger.info(f"✅ Verified {name}")
            except Exception as e:
                logger.error(f"Failed to checkpoint {name}: {e}")

    def shutdown(self, timeout: int = 15):
        """Gracefully shutdown worker threads and save pending data.

        This is the unified shutdown method that handles:
        1. Setting shutdown event to signal workers to stop
        2. Unblocking worker queues with None sentinel values
        3. Joining worker threads with timeout
        4. Checkpointing pending data

        Args:
            timeout: Maximum seconds to wait for workers to finish (default: 15)

        Note:
            Can be called multiple times safely (idempotent).
        """
        # Skip if workers were never started (e.g., in tests)
        if not self._workers_started:
            logger.debug("Workers were not started, skipping shutdown")
            return

        # Skip if already shut down
        if self.shutdown_event.is_set():
            logger.debug("Already shut down, skipping")
            return

        logger.info(f"🛑 Shutting down DeltaLakeManager (timeout: {timeout}s)...")

        # Signal workers to stop
        self.shutdown_event.set()

        # Unblock queues with sentinel values
        try:
            self.write_queue.put(None, timeout=1)
        except queue.Full:
            logger.warning("Write queue full, worker may be blocked")

        try:
            self.maintenance_queue.put(None, timeout=1)
        except queue.Full:
            logger.warning("Maintenance queue full, worker may be blocked")

        # Join worker threads with timeout
        threads_to_join = [
            (self.worker_thread, "write worker"),
            (self.maintenance_worker_thread, "maintenance worker"),
        ]

        for thread, name in threads_to_join:
            if thread and thread.is_alive():
                logger.debug(f"Waiting for {name} to finish...")
                thread.join(timeout=timeout / 2)  # Split timeout between workers

                if thread.is_alive():
                    logger.warning(f"⚠️  {name} did not stop in time")
                else:
                    logger.info(f"✅ {name} stopped gracefully")

        # Checkpoint pending data with shorter timeout
        self.checkpoint(timeout=min(timeout, 5))

        logger.info("✅ DeltaLakeManager shutdown complete")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown signals (SIGINT, SIGTERM) gracefully.

        This is registered as a signal handler and calls the unified shutdown() method.
        """
        signal_name = signal.Signals(signum).name
        logger.info(f"🛑 {signal_name} received, initiating graceful shutdown...")

        try:
            self.shutdown(timeout=15)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}", exc_info=True)
        finally:
            logger.info("✅ Shutdown handler complete")
            sys.exit(0)

    def list_tables(self) -> list[dict[str, Any]]:
        """List all tables with statistics."""
        tables_info = []

        for table_name, table_path in self.tables.items():
            parquet_files = list(table_path.glob("*.parquet"))

            info = {
                'name': table_name,
                'path': str(table_path),
                'exists': (table_path / "_delta_log").exists(),
                'parquet_files': len(parquet_files),
                'row_count': 0
            }

            if info['exists']:
                try:
                    info['row_count'] = self.count(table_name)
                except Exception as e:
                    info['error'] = str(e)

            tables_info.append(info)

        return tables_info

    def export(self, table_name: str, output_path: str, format: str = 'csv'):
        """Export table to file using streaming to reduce memory usage.

        Args:
            table_name: Name of table to export
            output_path: Output file path
            format: Output format ('csv', 'json', 'parquet')
        """
        import pyarrow as pa
        import pyarrow.csv as pa_csv
        import pyarrow.parquet as pq
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Gracefully handle non-existent tables
        if not (table_path / "_delta_log").exists():
            logger.warning(f"No data in table: {table_name}, exporting empty file.")
            pa_table = pa.Table.from_pylist([])
            row_count = 0
            col_count = 0
        else:
            # Read data using deltalake to respect the transaction log
            table = DeltaTable(str(table_path))
            pa_table = table.to_pyarrow_table()
            row_count = pa_table.num_rows
            col_count = len(pa_table.schema)

        # Stream data directly to file to reduce memory usage
        if format == 'csv':
            # Use PyArrow CSV writer for streaming
            with pa_csv.CSVWriter(output_path, pa_table.schema) as writer:
                writer.write_table(pa_table)
        elif format == 'json':
            # For JSON, convert to pandas only for compatibility
            # (PyArrow doesn't have native JSON streaming writer)
            result_df = pa_table.to_pandas()
            result_df.to_json(output_path, orient='records', lines=True)
        elif format == 'parquet':
            # Use PyArrow Parquet writer for streaming with compression
            pq.write_table(pa_table, output_path, compression='ZSTD')
        else:
            raise ValueError(f"Unsupported format: {format}")

        logger.info(f"✅ Exported {table_name} to {output_path} ({format})")

        return {
            'table': table_name,
            'output': str(output_path),
            'format': format,
            'rows': row_count,
            'columns': col_count,
            'size_mb': output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0,
        }

    def export_all(self, output_dir: str, format: str = 'csv') -> list[dict[str, Any]]:
        """Export all tables to directory.

        Args:
            output_dir: Output directory path
            format: Output format ('csv', 'json', 'parquet')

        Returns:
            List of export results
        """
        from pathlib import Path

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for table_name in self.tables.keys():
            try:
                output_path = output_dir / f"{table_name}.{format}"
                result = self.export(table_name, str(output_path), format)
                results.append(result)
            except Exception as e:
                logger.warning(f"Failed to export {table_name}: {e}")
                results.append({
                    'table': table_name,
                    'error': str(e)
                })

        return results


# Global singleton
_delta_manager = None


def get_delta_manager(base_path: str | None = None, start_workers: bool = True) -> DeltaLakeManager:
    """Get or create global Delta Lake manager.

    Args:
        base_path: Optional base path for Delta tables (overrides config)
        start_workers: If True, start background worker threads and register
                      signal handlers. Set to False for testing.

    Returns:
        Singleton DeltaLakeManager instance

    Note:
        Once created, the singleton instance persists. Subsequent calls with
        different parameters will return the existing instance without modification.
    """
    global _delta_manager
    if _delta_manager is None:
        _delta_manager = DeltaLakeManager(base_path=base_path, start_workers=start_workers)
    return _delta_manager
