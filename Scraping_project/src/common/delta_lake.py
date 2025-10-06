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
            'stage1_discovery': self.base_path / 'stage1_discovery',
            'stage1_errors': self.base_path / 'stage1_errors',
            'stage1_js_render_queue': self.base_path / 'stage1_js_render_queue',
            'stage2_page_analysis': self.base_path / 'stage2_page_analysis',
            'stage3_analytics': self.base_path / 'stage3_analytics',
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

        # Add metadata
        for record in data:
            if '_ingestion_time' not in record:
                record['_ingestion_time'] = datetime.now().isoformat()
            if '_stage' not in record:
                record['_stage'] = table_name

        # Convert to PyArrow Table
        table = pa.Table.from_pylist(data)

        # Write to Delta Lake
        write_deltalake(
            str(table_path),
            table,
            mode=mode,
            schema_mode="merge" if mode == "append" else "overwrite"
        )

        logger.info(f"✅ Wrote {len(data)} records to {table_name}")

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

    def checkpoint(self):
        """Wait for queue to finish and checkpoint all tables."""
        logger.info("Waiting for queue to finish...")
        self.write_queue.join()  # Wait for all tasks

        logger.info("Checkpointing all Delta tables...")
        for name, table_path in self.tables.items():
            try:
                if (table_path / "_delta_log").exists():
                    table = DeltaTable(str(table_path))
                    table.vacuum(retention_hours=168)  # 7 days
                    logger.info(f"✅ Checkpointed {name}")
            except Exception as e:
                logger.error(f"Failed to checkpoint {name}: {e}")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown gracefully."""
        logger.info("Shutdown signal received, checkpointing Delta Lake...")
        self.shutdown_event.set()
        self.write_queue.put(None)  # Signal worker to stop
        if self.worker_thread:
            self.worker_thread.join(timeout=10)
        self.checkpoint()
        sys.exit(0)


# Global singleton
_delta_manager = None


def get_delta_manager() -> DeltaLakeManager:
    """Get or create global Delta Lake manager."""
    global _delta_manager
    if _delta_manager is None:
        _delta_manager = DeltaLakeManager()
    return _delta_manager
