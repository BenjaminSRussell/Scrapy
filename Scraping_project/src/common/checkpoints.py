"""
Checkpoint system for resumable pipeline operations
"""
import json
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class BatchCheckpoint:
    """Manages batch processing checkpoints for resumable operations"""

    def __init__(self, checkpoint_file: Path):
        self.checkpoint_file = Path(checkpoint_file)
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load_checkpoint()

    def _load_checkpoint(self) -> Dict[str, Any]:
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
            with open(self.checkpoint_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.warning(f"Failed to load checkpoint {self.checkpoint_file}: {e}")
            return self._create_empty_checkpoint()

    def _create_empty_checkpoint(self) -> Dict[str, Any]:
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

    def start_batch(self, stage: str, batch_id: int, metadata: Dict[str, Any] = None):
        """Mark the start of a new batch"""
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

    def get_resume_point(self) -> Dict[str, Any]:
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

    def get_stats(self) -> Dict[str, Any]:
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

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
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