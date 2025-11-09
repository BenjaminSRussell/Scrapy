"""
Global Delta Lake utilities.

Centralizes all Delta Lake operations to eliminate duplicate code across the pipeline.
This module merges functionality from:
- src/common/delta_lake.py
- src/common/storage_manager.py
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class DeltaHelper:
    """Centralized Delta Lake operations."""

    def __init__(self, base_path: Optional[Path] = None):
        """
        Initialize Delta helper.

        Args:
            base_path: Base path for Delta Lake storage. Defaults to ./data/delta_lake
        """
        if base_path is None:
            base_path = Path("./data/delta_lake")

        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._manager = None

    @property
    def manager(self):
        """Lazy load lakehouse manager."""
        if self._manager is None:
            from src.lakehouse.lakehouse_manager import LakehouseManager
            self._manager = LakehouseManager(self.base_path)
        return self._manager

    def read(self, table_name: str, filters: Optional[List] = None) -> List[Dict]:
        """
        Read from Delta table.

        Args:
            table_name: Name of the table to read
            filters: Optional filters to apply

        Returns:
            List of dictionaries representing rows

        Example:
            delta = get_delta()
            seed_urls = delta.read("seed_urls")
        """
        try:
            return self.manager.read(table_name)
        except Exception as e:
            logger.error(f"Failed to read from {table_name}: {e}")
            return []

    def write(
        self,
        table_name: str,
        data: List[Dict],
        mode: str = "append"
    ) -> bool:
        """
        Write to Delta table.

        Args:
            table_name: Name of the table to write to
            data: List of dictionaries to write
            mode: Write mode ('append' or 'overwrite')

        Returns:
            True if successful, False otherwise

        Example:
            delta = get_delta()
            success = delta.write("stage1_discovery", urls, mode="append")
        """
        try:
            self.manager.write(table_name, data, mode=mode)
            return True
        except Exception as e:
            logger.error(f"Failed to write to {table_name}: {e}")
            return False

    def table_exists(self, table_name: str) -> bool:
        """
        Check if table exists.

        Args:
            table_name: Name of the table

        Returns:
            True if table exists, False otherwise
        """
        table_path = self.manager.get_table_path(table_name)
        return table_path.exists()

    def get_row_count(self, table_name: str) -> int:
        """
        Get total row count for a table.

        Args:
            table_name: Name of the table

        Returns:
            Number of rows, or 0 if error
        """
        try:
            data = self.read(table_name)
            return len(data)
        except Exception as e:
            logger.error(f"Failed to get row count for {table_name}: {e}")
            return 0

    def clear_table(self, table_name: str) -> bool:
        """
        Clear all data from a table.

        Args:
            table_name: Name of the table to clear

        Returns:
            True if successful, False otherwise
        """
        try:
            self.write(table_name, [], mode="overwrite")
            return True
        except Exception as e:
            logger.error(f"Failed to clear {table_name}: {e}")
            return False


# Global instance
_delta_helper: Optional[DeltaHelper] = None


def get_delta(base_path: Optional[Path] = None) -> DeltaHelper:
    """
    Get global Delta helper instance.

    This is the primary way to access Delta Lake operations throughout the pipeline.

    Args:
        base_path: Optional base path for Delta Lake storage

    Returns:
        DeltaHelper instance

    Example:
        from src.utils.delta import get_delta

        delta = get_delta()
        urls = delta.read("seed_urls")
        delta.write("stage1_discovery", new_urls)
    """
    global _delta_helper
    if _delta_helper is None:
        _delta_helper = DeltaHelper(base_path)
    return _delta_helper


def reset_delta():
    """Reset global Delta helper instance (useful for testing)."""
    global _delta_helper
    _delta_helper = None
