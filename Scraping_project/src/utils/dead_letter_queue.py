"""
Dead Letter Queue (DLQ) for capturing failed items.

Phase 7: Failed item management for manual review and replay.
"""

import json
import logging
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.exceptions import PipelineException

logger = logging.getLogger(__name__)


class DeadLetterQueue:
    """
    Captures failed items for manual review and replay.

    Failed items are written to JSON files with full context including:
    - Original item data
    - Error information
    - Retry count
    - Timestamp
    - Context (stage, correlation ID, etc.)
    """

    def __init__(self, base_path: Optional[Path] = None):
        if base_path is None:
            base_path = Path("./data/dlq")
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def add(
        self,
        item: Dict[str, Any],
        error: Exception,
        stage: str,
        context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Add failed item to DLQ.

        Args:
            item: The item that failed processing
            error: The exception that was raised
            stage: Pipeline stage where failure occurred
            context: Additional context (correlation_id, worker_id, etc.)

        Returns:
            Entry ID for tracking
        """
        # Build error information
        error_info = {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc()
        }

        # Add Pipeline exception details if available
        if isinstance(error, PipelineException):
            error_info.update({
                "category": error.category.value,
                "severity": error.severity.value,
                "retryable": error.retryable,
                "context": error.context
            })

        # Build DLQ entry
        dlq_entry = {
            "item": item,
            "error": error_info,
            "stage": stage,
            "context": context or {},
            "timestamp": datetime.now().isoformat(),
            "retry_count": item.get("_retry_count", 0),
            "url": item.get("url", "unknown"),
            "url_hash": item.get("url_hash", "unknown"),
        }

        # Generate unique ID
        entry_id = self._generate_entry_id(stage)
        file_path = self.base_path / f"{entry_id}.json"

        # Write to file
        try:
            with open(file_path, 'w') as f:
                json.dump(dlq_entry, f, indent=2)

            logger.error(
                f"Added item to DLQ: {entry_id} | "
                f"Stage: {stage} | "
                f"Error: {error_info['type']} | "
                f"URL: {dlq_entry['url'][:80] if dlq_entry['url'] != 'unknown' else 'unknown'}"
            )

            return entry_id

        except Exception as e:
            logger.error(f"Failed to write to DLQ: {e}")
            # Return empty string to indicate failure
            return ""

    def list_failed(
        self,
        stage: Optional[str] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        List all failed items, optionally filtered by stage.

        Args:
            stage: Filter by pipeline stage
            limit: Maximum number of items to return

        Returns:
            List of DLQ entries sorted by timestamp (newest first)
        """
        failed_items = []

        try:
            for file_path in self.base_path.glob("*.json"):
                try:
                    with open(file_path) as f:
                        entry = json.load(f)

                    # Filter by stage if specified
                    if stage is None or entry.get("stage") == stage:
                        entry["id"] = file_path.stem
                        entry["file_path"] = str(file_path)
                        failed_items.append(entry)

                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse DLQ file {file_path}: {e}")
                except Exception as e:
                    logger.error(f"Error reading DLQ file {file_path}: {e}")

            # Sort by timestamp (newest first)
            failed_items.sort(
                key=lambda x: x.get("timestamp", ""),
                reverse=True
            )

            # Apply limit if specified
            if limit:
                failed_items = failed_items[:limit]

            return failed_items

        except Exception as e:
            logger.error(f"Failed to list DLQ items: {e}")
            return []

    def replay(self, entry_id: str) -> Optional[Dict[str, Any]]:
        """
        Get item for replay.

        Args:
            entry_id: DLQ entry ID

        Returns:
            Item data with incremented retry count, or None if not found
        """
        file_path = self.base_path / f"{entry_id}.json"

        if not file_path.exists():
            logger.error(f"DLQ entry not found: {entry_id}")
            return None

        try:
            with open(file_path) as f:
                entry = json.load(f)

            # Get original item and increment retry count
            item = entry["item"].copy()
            item["_retry_count"] = entry.get("retry_count", 0) + 1
            item["_dlq_entry_id"] = entry_id
            item["_replayed_at"] = datetime.now().isoformat()

            logger.info(
                f"Replaying DLQ entry: {entry_id} | "
                f"Stage: {entry.get('stage')} | "
                f"Retry: {item['_retry_count']}"
            )

            return item

        except Exception as e:
            logger.error(f"Failed to replay DLQ entry {entry_id}: {e}")
            return None

    def resolve(self, entry_id: str, success: bool = True) -> bool:
        """
        Mark item as resolved and move to resolved folder.

        Args:
            entry_id: DLQ entry ID
            success: Whether resolution was successful

        Returns:
            True if resolved successfully
        """
        file_path = self.base_path / f"{entry_id}.json"

        if not file_path.exists():
            logger.error(f"DLQ entry not found: {entry_id}")
            return False

        try:
            # Create resolved directory
            resolved_dir = self.base_path / ("resolved" if success else "failed")
            resolved_dir.mkdir(exist_ok=True)

            # Move file
            destination = resolved_dir / file_path.name
            file_path.rename(destination)

            logger.info(
                f"Resolved DLQ entry: {entry_id} | "
                f"Success: {success}"
            )

            return True

        except Exception as e:
            logger.error(f"Failed to resolve DLQ entry {entry_id}: {e}")
            return False

    def get_stats(self) -> Dict[str, Any]:
        """
        Get DLQ statistics.

        Returns:
            Dict with counts by stage and overall stats
        """
        try:
            all_items = self.list_failed()

            stats = {
                "total_failed": len(all_items),
                "by_stage": {},
                "by_error_type": {},
                "oldest_failure": None,
                "newest_failure": None,
            }

            if all_items:
                # Group by stage
                for item in all_items:
                    stage = item.get("stage", "unknown")
                    stats["by_stage"][stage] = stats["by_stage"].get(stage, 0) + 1

                    # Group by error type
                    error_type = item.get("error", {}).get("type", "unknown")
                    stats["by_error_type"][error_type] = stats["by_error_type"].get(error_type, 0) + 1

                # Get oldest and newest
                stats["oldest_failure"] = all_items[-1].get("timestamp")
                stats["newest_failure"] = all_items[0].get("timestamp")

            return stats

        except Exception as e:
            logger.error(f"Failed to get DLQ stats: {e}")
            return {"total_failed": 0, "error": str(e)}

    def cleanup_old(self, days: int = 30) -> int:
        """
        Remove DLQ entries older than specified days.

        Args:
            days: Age threshold in days

        Returns:
            Number of entries removed
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=days)
            removed_count = 0

            for file_path in self.base_path.glob("*.json"):
                try:
                    with open(file_path) as f:
                        entry = json.load(f)

                    timestamp = datetime.fromisoformat(entry.get("timestamp", ""))
                    if timestamp < cutoff_date:
                        file_path.unlink()
                        removed_count += 1

                except Exception as e:
                    logger.debug(f"Error checking file {file_path}: {e}")

            if removed_count > 0:
                logger.info(f"Cleaned up {removed_count} old DLQ entries")

            return removed_count

        except Exception as e:
            logger.error(f"Failed to cleanup old DLQ entries: {e}")
            return 0

    def _generate_entry_id(self, stage: str) -> str:
        """Generate unique entry ID."""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = uuid.uuid4().hex[:8]
        return f"{stage}_{timestamp}_{unique_id}"


# Global DLQ instance
_global_dlq: Optional[DeadLetterQueue] = None


def get_dlq(base_path: Optional[Path] = None) -> DeadLetterQueue:
    """Get global DLQ instance."""
    global _global_dlq
    if _global_dlq is None:
        _global_dlq = DeadLetterQueue(base_path)
    return _global_dlq
