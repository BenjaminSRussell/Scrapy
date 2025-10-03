# TODO: Consider using a more robust storage for checkpoints, like a database, to avoid issues with file corruption and to make it easier to query the status of checkpoints.
"""
Checkpoint system for resumable pipeline operations
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class BatchCheckpoint:
    """Manages batch processing checkpoints for resumable operations"""

    def __init__(self, checkpoint_file: Path):
        self.checkpoint_file = Path(checkpoint_file)
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load_checkpoint()

    def _load_checkpoint(self) -> dict[str, Any]:
        """Load existing checkpoint data"""
        if not self.checkpoint_file.exists():
            return {
                'stage': None,
                'batch_id': 0,
                'last_processed_line': 0,
                'last_url_hash': None,
                'total_processed': 0,
                'created_at': datetime.now().isoformat(),
                'updated_at': datetime.now().isoformat(),
                'status': 'initialized',
                'metadata': {}
            }

        try:
            with open(self.checkpoint_file, encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"Failed to load checkpoint {self.checkpoint_file}: {e}")
            return self._create_empty_checkpoint()

    def _create_empty_checkpoint(self) -> dict[str, Any]:
        """Create empty checkpoint structure"""
        return {
            'stage': None,
            'batch_id': 0,
            'last_processed_line': 0,
            'last_url_hash': None,
            'total_processed': 0,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'status': 'initialized',
            'metadata': {}
        }

    def save_checkpoint(self):
        """Save current checkpoint state to disk"""
        self._data['updated_at'] = datetime.now().isoformat()

        try:
            with open(self.checkpoint_file, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            logger.debug(f"Checkpoint saved: {self.checkpoint_file}")
        except Exception as e:
            logger.error(f"Failed to save checkpoint {self.checkpoint_file}: {e}")

    def start_batch(self, stage: str, batch_id: int, metadata: dict[str, Any] = None):
        """Mark the start of a new batch"""
        # TODO: Add more context to the checkpoint metadata, such as the input file being processed.
        self._data.update({
            'stage': stage,
            'batch_id': batch_id,
            'status': 'processing',
            'metadata': metadata or {}
        })
        self.save_checkpoint()

    def update_progress(self, processed_line: int, url_hash: str = None,
                       total_processed: int = None):
        """Update processing progress"""
        self._data['last_processed_line'] = processed_line
        if url_hash:
            self._data['last_url_hash'] = url_hash
        if total_processed is not None:
            self._data['total_processed'] = total_processed

        # TODO: The frequency of saving checkpoints is hardcoded to every 100 updates. This should be configurable.
        # save every 100 updates to avoid constant disk I/O
        if processed_line % 100 == 0:
            self.save_checkpoint()

    def complete_batch(self, total_processed: int):
        """Mark batch as completed"""
        self._data.update({
            'status': 'completed',
            'total_processed': total_processed,
            'batch_id': self._data.get('batch_id', 0) + 1
        })
        self.save_checkpoint()

    def mark_failed(self, error_message: str):
        """Mark checkpoint as failed"""
        self._data.update({
            'status': 'failed',
            'error_message': error_message
        })
        self.save_checkpoint()

    def get_resume_point(self) -> dict[str, Any]:
        """Get information needed to resume processing"""
        return {
            'stage': self._data.get('stage'),
            'batch_id': self._data.get('batch_id', 0),
            'last_processed_line': self._data.get('last_processed_line', 0),
            'last_url_hash': self._data.get('last_url_hash'),
            'total_processed': self._data.get('total_processed', 0),
            'status': self._data.get('status', 'initialized')
        }

    def should_skip_to_line(self, line_number: int) -> bool:
        """Check if we should skip to a specific line on resume"""
        return line_number <= self._data.get('last_processed_line', 0)

    def is_completed(self) -> bool:
        """Check if checkpoint indicates completion"""
        return self._data.get('status') == 'completed'

    def is_failed(self) -> bool:
        """Check if checkpoint indicates failure"""
        return self._data.get('status') == 'failed'

    def is_stale(self, max_age_hours: int = 24) -> bool:
        """Check if checkpoint is stale based on last update time.

        Args:
            max_age_hours: Maximum age in hours before checkpoint is considered stale

        Returns:
            True if checkpoint is older than max_age_hours
        """
        updated_at_str = self._data.get('updated_at')
        if not updated_at_str:
            return False

        try:
            updated_at = datetime.fromisoformat(updated_at_str)
            age = datetime.now() - updated_at
            return age > timedelta(hours=max_age_hours)
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse checkpoint timestamp: {e}")
            return False

    def validate_checkpoint(self, input_file: Path = None, max_age_hours: int = 24) -> tuple[bool, str]:
        """Validate checkpoint integrity and freshness.

        Args:
            input_file: Optional input file to verify checkpoint matches current data
            max_age_hours: Maximum age in hours before checkpoint is considered stale

        Returns:
            Tuple of (is_valid, reason)
        """
        # Check if checkpoint is stale
        if self.is_stale(max_age_hours):
            return False, f"Checkpoint is stale (older than {max_age_hours} hours)"

        # If checkpoint failed, it's invalid
        if self.is_failed():
            return False, f"Checkpoint marked as failed: {self._data.get('error_message', 'Unknown error')}"

        # Validate input file if provided
        if input_file and input_file.exists():
            input_hash = self._data.get('metadata', {}).get('input_file_hash')
            if input_hash:
                current_hash = self._compute_file_hash(input_file)
                if current_hash != input_hash:
                    return False, "Input file has changed since checkpoint was created"

        return True, "Checkpoint is valid"

    @staticmethod
    def _compute_file_hash(file_path: Path, chunk_size: int = 8192) -> str:
        """Compute SHA256 hash of a file."""
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest()
        except Exception as e:
            logger.error(f"Failed to compute file hash: {e}")
            return ""

    def get_stats(self) -> dict[str, Any]:
        """Get checkpoint statistics"""
        return {
            'stage': self._data.get('stage'),
            'batch_id': self._data.get('batch_id', 0),
            'total_processed': self._data.get('total_processed', 0),
            'status': self._data.get('status', 'unknown'),
            'created_at': self._data.get('created_at'),
            'updated_at': self._data.get('updated_at'),
            'metadata': self._data.get('metadata', {})
        }

    def reset(self):
        """Reset checkpoint to initial state"""
        self._data = self._create_empty_checkpoint()
        self.save_checkpoint()


class CheckpointManager:
    """Manages multiple stage checkpoints"""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoints = {}

    def get_checkpoint(self, stage: str) -> BatchCheckpoint:
        """Get or create checkpoint for a stage"""
        if stage not in self._checkpoints:
            checkpoint_file = self.base_dir / f"{stage}.checkpoint.json"
            self._checkpoints[stage] = BatchCheckpoint(checkpoint_file)
        return self._checkpoints[stage]

    def get_all_stats(self) -> dict[str, dict[str, Any]]:
        """Get stats for all stage checkpoints"""
        stats = {}

        # find all checkpoint files
        for checkpoint_file in self.base_dir.glob("*.checkpoint.json"):
            stage = checkpoint_file.stem.replace('.checkpoint', '')
            checkpoint = self.get_checkpoint(stage)
            stats[stage] = checkpoint.get_stats()

        return stats

    def reset_all(self):
        """Reset all checkpoints"""
        for checkpoint in self._checkpoints.values():
            checkpoint.reset()

    def cleanup_old_checkpoints(self, keep_days: int = 7):
        """Remove checkpoint files older than specified days"""
        import time
        cutoff_time = time.time() - (keep_days * 24 * 60 * 60)

        for checkpoint_file in self.base_dir.glob("*.checkpoint.json"):
            if checkpoint_file.stat().st_mtime < cutoff_time:
                try:
                    checkpoint_file.unlink()
                    logger.info(f"Cleaned up old checkpoint: {checkpoint_file}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup checkpoint {checkpoint_file}: {e}")