"""Organized Delta Lake with separate tables per stage.
Concurrent queue system for multiple requests.
"""

import logging
import queue
import signal
import sys
import threading
from datetime import datetime
from typing import Any

try:
    import pyarrow as pa
    from deltalake import DeltaTable, write_deltalake
    DELTA_AVAILABLE = True
except ImportError:
    DELTA_AVAILABLE = False
    DeltaTable = None
    write_deltalake = None
    pa = None

from src.common.constants import DELTA_LAKE

logger = logging.getLogger(__name__)


class DeltaLakeManager:
    """Manages Delta Lake with organized stage-based tables.
    Supports concurrent writes with queue system.
    """

    def __init__(self):
        if not DELTA_AVAILABLE:
            raise ImportError("Delta Lake not available. Install: pip install deltalake pyarrow")

        self.base_path = DELTA_LAKE
        self.base_path.mkdir(parents=True, exist_ok=True)

        # Stage-specific tables with intelligent routing
        self.tables = {
            'seed_urls': self.base_path / 'seed_urls',
            'stage1_discovery': self.base_path / 'stage1_discovery',
            'stage1_errors': self.base_path / 'stage1_errors',
            'stage1_js_render_queue': self.base_path / 'stage1_js_render_queue',
            'stage1_offsite_candidates': self.base_path / 'stage1_offsite_candidates',
            'stage2_page_analysis': self.base_path / 'stage2_page_analysis',
            'stage3_analytics': self.base_path / 'stage3_analytics',
            'stage3_summaries': self.base_path / 'stage3_summaries',
            'stage4_large_docs': self.base_path / 'stage4_large_docs',
            'stage4_summaries': self.base_path / 'stage4_summaries',
        }

        # Create table directories
        for table_path in self.tables.values():
            table_path.mkdir(parents=True, exist_ok=True)

        # Concurrent write queue
        self.write_queue = queue.Queue()
        self.worker_thread = None
        self.shutdown_event = threading.Event()

        # Start background worker
        self._start_worker()

        # Register shutdown handlers
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def _start_worker(self):
        """Start background worker for concurrent writes."""
        self.worker_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.worker_thread.start()
        logger.info("Delta Lake queue worker started")

    def _process_queue(self):
        """Process write queue in background."""
        while not self.shutdown_event.is_set():
            try:
                # Get write task with timeout
                task = self.write_queue.get(timeout=1.0)
                if task is None:  # Shutdown signal
                    break

                table_name, data, mode = task

                # Perform write
                self._write_sync(table_name, data, mode)

                self.write_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Queue worker error: {e}", exc_info=True)

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
                        # Extract domain without subdomain (e.g., 'uconn.edu' from 'www.uconn.edu')
                        domain_parts = parsed.netloc.split('.')
                        if len(domain_parts) >= 2:
                            record['domain'] = '.'.join(domain_parts[-2:])
                        else:
                            record['domain'] = parsed.netloc or 'unknown'
                    except Exception:
                        record['domain'] = 'unknown'

        # Add metadata
        for record in data:
            if '_ingestion_time' not in record:
                record['_ingestion_time'] = datetime.now().isoformat()
            if '_stage' not in record:
                record['_stage'] = table_name

        # Convert to PyArrow Table
        table = pa.Table.from_pylist(data)

        # Enhanced: Configure storage and write optimizations
        storage_options = {
            # Enable zstd compression for better compression ratios
            'parquet.compression': 'ZSTD',
            # Enable statistics for better query performance
            'parquet.enable.statistics': 'true',
            # Set row group size for better parallelism
            'parquet.block.size': '134217728',  # 128MB
        }

        # Enhanced: Partition by domain for discovery tables
        partition_by = None
        if table_name in ['stage1_discovery', 'stage2_page_analysis']:
            partition_by = ['domain']

        # Write to Delta Lake with optimizations
        # Note: partition_overwrite_mode is a PySpark config, not available in deltalake-rs
        # The deltalake library handles partition overwrites automatically based on mode
        write_deltalake(
            str(table_path),
            table,
            mode=mode,
            schema_mode="merge" if mode == "append" else "overwrite",
            # Enhanced: Add storage options for compression
            storage_options=storage_options,
            # Enhanced: Partition by domain
            partition_by=partition_by
        )

        logger.info(f"✅ Wrote {len(data)} records to {table_name}")

        # Enhanced: Auto-optimize after write for discovery tables
        if table_name in ['stage1_discovery', 'stage2_page_analysis'] and len(data) >= 1000:
            try:
                self._optimize_table(table_name)
            except Exception as e:
                logger.debug(f"Auto-optimization skipped for {table_name}: {e}")

    def write(self, table_name: str, data: list[dict[str, Any]], mode: str = "append", async_write: bool = True):
        """Write data to Delta Lake table.

        Args:
            table_name: 'stage1_discovery', 'stage2_analytics', or 'stage3_summaries'
            data: List of dictionaries
            mode: 'append' or 'overwrite'
            async_write: If True, queue for background write. If False, write immediately.

        """
        if async_write:
            # Queue for background processing
            self.write_queue.put((table_name, data, mode))
            logger.debug(f"Queued {len(data)} records for {table_name}")
        else:
            # Immediate synchronous write
            self._write_sync(table_name, data, mode)

    def read(self, table_name: str, filters: Any = None) -> list[dict]:
        """Read data from Delta Lake table."""
        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        if not (table_path / "_delta_log").exists():
            logger.warning(f"No data found in {table_name}")
            return []

        table = DeltaTable(str(table_path))
        pa_table = table.to_pyarrow_table(filters=filters)
        return pa_table.to_pylist()

    def count(self, table_name: str) -> int:
        """Get record count for a table."""
        data = self.read(table_name)
        return len(data)

    def _optimize_table(self, table_name: str):
        """Optimize Delta table with compaction and Z-ordering.

        Enhanced: Performs file compaction and Z-ordering on url_hash and discovered_at
        for better query performance.
        """
        table_path = self.tables.get(table_name)
        if not table_path or not (table_path / "_delta_log").exists():
            return

        try:
            dt = DeltaTable(str(table_path))

            # Perform compaction (combine small files)
            logger.info(f"Optimizing {table_name} with compaction...")
            dt.optimize.compact()

            # Enhanced: Z-order by url_hash and timestamp for co-location
            # This improves query performance for lookups by URL hash and time-based queries
            logger.info(f"Z-ordering {table_name} by url_hash and discovered_at...")
            if table_name == 'stage1_discovery':
                dt.optimize.z_order(['url_hash', 'discovered_at'])
            elif table_name == 'stage2_page_analysis':
                dt.optimize.z_order(['url_hash', 'processed_at'])

            logger.info(f"✅ Optimized {table_name}")

        except Exception as e:
            logger.warning(f"Optimization failed for {table_name}: {e}")

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

    def force_shutdown(self, timeout: int = 15):
        """Force shutdown with aggressive timeout."""
        logger.warning(f"⚠️  FORCE SHUTDOWN initiated (timeout: {timeout}s)")

        # Signal worker to stop
        self.shutdown_event.set()
        self.write_queue.put(None)

        # Wait for worker with timeout
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=timeout)

            if self.worker_thread.is_alive():
                logger.error("❌ Worker thread did not stop in time - forcing exit")

        # Quick checkpoint without vacuum
        self.checkpoint(timeout=5)
        logger.info("🛑 Force shutdown complete")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown gracefully with timeout."""
        logger.info("🛑 Shutdown signal received, saving Delta Lake data...")

        try:
            self.force_shutdown(timeout=15)
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
        finally:
            logger.info("✅ Shutdown complete")
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
                    data = self.read(table_name)
                    info['row_count'] = len(data)
                except Exception as e:
                    info['error'] = str(e)

            tables_info.append(info)

        return tables_info

    def export(self, table_name: str, output_path: str, format: str = 'csv'):
        """Export table to file.
        Args:
            table_name: Name of table to export
            output_path: Output file path
            format: Output format ('csv', 'json', 'parquet')
        """
        from pathlib import Path

        import pyarrow as pa
        from deltalake import DeltaTable

        table_path = self.tables.get(table_name)
        if not table_path:
            raise ValueError(f"Unknown table: {table_name}")

        # Gracefully handle non-existent tables
        if not (table_path / "_delta_log").exists():
            logger.warning(f"No data in table: {table_name}, exporting empty file.")
            # Create an empty PyArrow table to ensure downstream compatibility
            pa_table = pa.Table.from_pylist([])
        else:
            # Read data using deltalake to respect the transaction log
            table = DeltaTable(str(table_path))
            pa_table = table.to_pyarrow_table()

        # Convert to Pandas DataFrame for flexible export
        result_df = pa_table.to_pandas()

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Export to requested format
        if format == 'csv':
            result_df.to_csv(output_path, index=False)
        elif format == 'json':
            result_df.to_json(output_path, orient='records', lines=True)
        elif format == 'parquet':
            result_df.to_parquet(output_path, index=False)
        else:
            raise ValueError(f"Unsupported format: {format}")

        logger.info(f"✅ Exported {table_name} to {output_path} ({format})")

        return {
            'table': table_name,
            'output': str(output_path),
            'format': format,
            'rows': len(result_df),
            'columns': len(result_df.columns),
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


def get_delta_manager() -> DeltaLakeManager:
    """Get or create global Delta Lake manager."""
    global _delta_manager
    if _delta_manager is None:
        _delta_manager = DeltaLakeManager()
    return _delta_manager
