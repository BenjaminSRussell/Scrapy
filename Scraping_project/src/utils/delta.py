"""
Global Delta Lake utilities.

Centralizes all Delta Lake operations to eliminate duplicate code across the pipeline.
This module merges functionality from:
- src/common/delta_lake.py (removed; use src.lakehouse.lakehouse_manager)
- src/common/storage_manager.py

Phase 6 Enhancement: Added type-safe operations with Pydantic validation
"""

from typing import TYPE_CHECKING, List, Dict, Optional, TypeVar, Type, Union
from pathlib import Path
import logging
import os
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from src.lakehouse.lakehouse_manager import LakehouseManager, WriteMode

logger = logging.getLogger(__name__)

T = TypeVar('T', bound=BaseModel)


class DeltaHelper:
    """Centralized Delta Lake operations.

    ``get_delta()`` is the single public write API for lake tables (#359).
    Its helper is *shared*: it delegates to ``LakehouseManager.get_instance()``,
    the same manager ``get_lakehouse_manager()`` / ``get_delta_manager()`` /
    ``lakehouse_session()`` return. One process therefore has one write queue,
    one writer thread and one schema cache, rather than two managers that
    disagree. A ``DeltaHelper(path)`` constructed directly (tests, tools on
    another lake) keeps its own private manager.

    Writes are explicit about durability: ``write(..., async_write=True)``
    (default) queues the batch on the manager's writer thread and returns once
    queued; ``async_write=False`` writes synchronously and returns whether
    the rows are committed. Both return False instead of raising.
    """

    def __init__(self, base_path: Optional[Union[str, Path]] = None, shared: bool = False):
        """
        Initialize Delta helper.

        Args:
            base_path: Base path for Delta Lake storage. Defaults to the
                DELTA_LAKE_PATH env var, then config ``delta_lake.base_path``,
                then ./data/delta_lake (same order as LakehouseManager).
            shared: Delegate to the process-wide ``LakehouseManager``
                singleton (what ``get_delta()`` does) instead of a private one.
        """
        if base_path is None:
            base_path = os.getenv("DELTA_LAKE_PATH")
        if not base_path:
            from src.core.config import get_config

            base_path = get_config().get("delta_lake.base_path", "./data/delta_lake")

        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.shared = shared
        self._manager: Optional["LakehouseManager"] = None
        # The singleton this helper last attached to; any other non-None
        # _manager was set explicitly (tests, tools) and is left alone.
        self._attached: Optional["LakehouseManager"] = None

    @property
    def manager(self) -> "LakehouseManager":
        """The lakehouse manager this helper writes through (lazy)."""
        from src.lakehouse.lakehouse_manager import LakehouseManager

        if self.shared:
            if self._manager is not None and self._manager is not self._attached:
                return self._manager  # injected explicitly, or a private manager for another lake
            current = LakehouseManager._instance
            if current is not None and self._manager is current:
                return current
            if current is None or _same_path(current.base_path, self.base_path):
                # (Re)attach to the singleton, e.g. after lakehouse_session()
                # reset it, so we never write through a shut-down manager.
                self._manager = self._attached = LakehouseManager.get_instance(base_path=str(self.base_path))
                return self._manager
            if self._manager is None:
                logger.warning(
                    f"LakehouseManager singleton is on {current.base_path}, not {self.base_path}; "
                    "get_delta() uses a separate manager for its lake (#359)"
                )
                self._manager = LakehouseManager(str(self.base_path))
            return self._manager
        if self._manager is None:
            self._manager = LakehouseManager(str(self.base_path))
        return self._manager

    def read(
        self,
        table_name: str,
        filters: Optional[List] = None,
        columns: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Read from Delta table.

        Args:
            table_name: Name of the table to read
            filters: Optional filters to apply
            columns: Optional subset of columns to read

        Returns:
            List of dictionaries representing rows

        Example:
            delta = get_delta()
            seed_urls = delta.read("seed_urls")
        """
        try:
            return self.manager.read(table_name, filters=filters, columns=columns)
        except Exception as e:
            logger.error(f"Failed to read from {table_name}: {e}")
            return []

    def read_table(self, table_name: str, **kwargs) -> List[Dict]:
        """
        Proxy for LakehouseManager.read_table (Stage2-style API).

        Args:
            table_name: Name of the table to read
            **kwargs: Optional filters/columns forwarded to the manager

        Returns:
            List of dictionaries representing rows (empty list on error / missing data)
        """
        try:
            return self.manager.read_table(table_name, **kwargs)
        except Exception as e:
            logger.error(f"Failed to read_table from {table_name}: {e}")
            return []

    def write(
        self,
        table_name: str,
        data: List[Dict],
        mode: "WriteMode" = "append",
        async_write: bool = True,
    ) -> bool:
        """
        Write to Delta table.

        Args:
            table_name: Name of the table to write to
            data: List of dictionaries to write
            mode: Write mode ('append' or 'overwrite')
            async_write: If True, queue the write; if False, write synchronously

        Returns:
            True if successful, False otherwise

        Example:
            delta = get_delta()
            success = delta.write("stage1_discovery", urls, mode="append")
            delta.write("stage2_page_analysis", rows, mode="append", async_write=False)
        """
        try:
            # False = not written: failed sync write, or async write spilled
            # because the queue stayed full (#167/#225). None (older backends) = ok.
            result = self.manager.write(table_name, data, mode=mode, async_write=async_write)
            return result is not False
        except Exception as e:
            logger.error(f"Failed to write to {table_name}: {e}")
            return False

    def merge_into(
        self,
        table_name: str,
        updates_data: List[Dict],
        merge_key: Union[str, List[str]],
        update_columns: List[str],
    ) -> int:
        """Upsert rows by key via Delta MERGE (see LakehouseManager.merge_into).

        Returns rows updated + inserted, or -1 on failure (nothing committed).
        """
        try:
            return self.manager.merge_into(table_name, updates_data, merge_key, update_columns)
        except Exception as e:
            logger.error(f"Failed to merge into {table_name}: {e}")
            return -1

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
            return self.manager.count(table_name)
        except Exception as e:
            logger.error(f"Failed to get row count for {table_name}: {e}")
            return 0

    def count(self, table_name: str) -> int:
        """Alias of get_row_count (LakehouseManager-compatible name)."""
        return self.get_row_count(table_name)

    def clear_table(self, table_name: str) -> bool:
        """
        Clear all data from a table.

        Args:
            table_name: Name of the table to clear

        Returns:
            True if successful, False otherwise
        """
        try:
            # Writing [] used to be a silent no-op (empty batches short-circuit);
            # a Delta DELETE empties the table and keeps its history (#614).
            truncate = getattr(self.manager, "truncate_table", None)
            if truncate is None:
                logger.error(f"Failed to clear {table_name}: backend has no truncate_table")
                return False
            return bool(truncate(table_name))
        except Exception as e:
            logger.error(f"Failed to clear {table_name}: {e}")
            return False

    # Phase 6: Type-safe operations with Pydantic validation

    def read_typed(
        self,
        table_name: str,
        model: Type[T],
        filters: Optional[List] = None,
        validate_all: bool = True
    ) -> List[T]:
        """
        Read and validate data using Pydantic model.

        Args:
            table_name: Name of the table to read
            model: Pydantic model class for validation
            filters: Optional filters to apply
            validate_all: If False, skip invalid rows instead of failing

        Returns:
            List of validated Pydantic model instances

        Example:
            from src.core.models import Stage2Analysis
            delta = get_delta()
            analyses = delta.read_typed("stage2_page_analysis", Stage2Analysis)

        Raises:
            ValidationError: If validation fails and validate_all=True
        """
        raw_data = self.read(table_name, filters)

        validated_data = []
        validation_errors = []

        for idx, row in enumerate(raw_data):
            try:
                validated_item = model(**row)
                validated_data.append(validated_item)
            except ValidationError as e:
                validation_errors.append((idx, row, e))
                if validate_all:
                    logger.error(
                        f"Validation failed for row {idx} in {table_name}: {e}"
                    )
                    raise
                else:
                    logger.warning(
                        f"Skipping invalid row {idx} in {table_name}: {e}"
                    )

        if validation_errors and not validate_all:
            logger.warning(
                f"Skipped {len(validation_errors)} invalid rows out of "
                f"{len(raw_data)} total in {table_name}"
            )

        return validated_data

    def write_typed(
        self,
        table_name: str,
        data: List[T],
        mode: "WriteMode" = "append"
    ) -> bool:
        """
        Write validated Pydantic models to Delta table.

        Args:
            table_name: Name of the table to write to
            data: List of Pydantic model instances
            mode: Write mode ('append' or 'overwrite')

        Returns:
            True if successful, False otherwise

        Example:
            from src.core.models import Stage2Analysis
            delta = get_delta()
            analysis = Stage2Analysis(url="https://...", ...)
            delta.write_typed("stage2_page_analysis", [analysis])
        """
        try:
            # Convert Pydantic models to dicts
            dict_data = [item.model_dump() for item in data]
            return self.write(table_name, dict_data, mode=mode)
        except Exception as e:
            logger.error(f"Failed to write typed data to {table_name}: {e}")
            return False

    def get_table_path(self, table_name: str) -> Path:
        """
        Get file system path for a table.

        Args:
            table_name: Name of the table

        Returns:
            Path to the table directory
        """
        return self.manager.get_table_path(table_name)


def _same_path(a: Union[str, Path], b: Union[str, Path]) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


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
        _delta_helper = DeltaHelper(base_path, shared=True)
    return _delta_helper


def reset_delta():
    """Reset global Delta helper instance (useful for testing)."""
    global _delta_helper
    _delta_helper = None

# Canonical factory is get_delta; alias kept for older call sites (#294).
get_delta_manager = get_delta
