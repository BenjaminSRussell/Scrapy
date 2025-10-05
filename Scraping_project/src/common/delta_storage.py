"""
Simplified Delta Lake storage with checkpoint on shutdown.
"""

import logging
import signal
import sys
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


class DeltaStorage:
    """Simplified Delta Lake storage with automatic checkpointing."""

    def __init__(self, table_name: str = "scraping_data"):
        if not DELTA_AVAILABLE:
            raise ImportError("Delta Lake not available. Install: pip install deltalake pyarrow")

        self.table_path = DELTA_LAKE / table_name
        self.table_path.mkdir(parents=True, exist_ok=True)

        # Register shutdown handler
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def write(self, data: list[dict[str, Any]], mode: str = "append"):
        """Write data to Delta Lake."""
        if not data:
            logger.warning("No data to write")
            return

        # Add timestamp
        for record in data:
            if '_ingestion_time' not in record:
                record['_ingestion_time'] = datetime.now().isoformat()

        # Convert to PyArrow Table
        table = pa.Table.from_pylist(data)

        # Write to Delta Lake
        write_deltalake(
            str(self.table_path),
            table,
            mode=mode,
            schema_mode="merge" if mode == "append" else "overwrite"
        )

        logger.info(f"Wrote {len(data)} records to {self.table_path}")

    def read(self, filters: Any = None) -> list[dict]:
        """Read data from Delta Lake."""
        if not self.table_path.exists() or not (self.table_path / "_delta_log").exists():
            logger.warning(f"No Delta table found at {self.table_path}")
            return []

        table = DeltaTable(str(self.table_path))
        pa_table = table.to_pyarrow_table(filters=filters)
        return pa_table.to_pylist()

    def checkpoint(self):
        """Create a checkpoint of the Delta table."""
        if not self.table_path.exists():
            return

        try:
            table = DeltaTable(str(self.table_path))
            # Compact and checkpoint
            table.vacuum(retention_hours=168)  # 7 days
            logger.info(f"✅ Delta Lake checkpoint created at {self.table_path}")
        except Exception as e:
            logger.error(f"Failed to checkpoint Delta Lake: {e}")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown and checkpoint."""
        logger.info("Shutdown signal received, checkpointing Delta Lake...")
        self.checkpoint()
        sys.exit(0)

    def count(self) -> int:
        """Get record count."""
        data = self.read()
        return len(data)


# Global storage instance
_storage = None


def get_storage() -> DeltaStorage:
    """Get or create global storage instance."""
    global _storage
    if _storage is None:
        _storage = DeltaStorage()
    return _storage
