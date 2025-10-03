"""
Enhanced checkpoint system with unified handling, crash recovery, and progress reporting.

Provides:
- Unified checkpoint management across all pipeline stages
- Automatic crash recovery with state validation
- Progress reporting and estimation
- Atomic checkpoint updates
- Checkpoint health monitoring
"""

import hashlib
import json
import logging
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CheckpointStatus(Enum):
    """Checkpoint status states"""
    INITIALIZED = "initialized"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    RECOVERING = "recovering"


@dataclass
class ProgressMetrics:
    """Progress tracking metrics"""
    total_items: int = 0
    processed_items: int = 0
    successful_items: int = 0
    failed_items: int = 0
    skipped_items: int = 0
    start_time: float | None = None
    last_update_time: float | None = None
    estimated_completion_time: float | None = None

    def get_progress_percentage(self) -> float:
        """Calculate progress percentage"""
        if self.total_items == 0:
            return 0.0
        return (self.processed_items / self.total_items) * 100

    def get_success_rate(self) -> float:
        """Calculate success rate"""
        if self.processed_items == 0:
            return 100.0
        return (self.successful_items / self.processed_items) * 100

    def get_throughput(self) -> float:
        """Calculate items processed per second"""
        if not self.start_time or not self.last_update_time:
            return 0.0

        elapsed = self.last_update_time - self.start_time
        if elapsed == 0:
            return 0.0

        return self.processed_items / elapsed

    def estimate_completion(self) -> float | None:
        """Estimate completion time in seconds"""
        if self.total_items == 0 or self.processed_items == 0:
            return None

        throughput = self.get_throughput()
        if throughput == 0:
            return None

        remaining = self.total_items - self.processed_items
        return remaining / throughput

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)


@dataclass
class CheckpointState:
    """Comprehensive checkpoint state"""
    stage: str
    status: CheckpointStatus = CheckpointStatus.INITIALIZED
    progress: ProgressMetrics = field(default_factory=ProgressMetrics)

    # Resume information
    last_processed_item: str | None = None
    last_processed_index: int = 0
    batch_id: int = 0

    # Metadata
    input_file: str | None = None
    input_file_hash: str | None = None
    output_file: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Error tracking
    error_message: str | None = None
    error_count: int = 0
    last_error_time: str | None = None

    # Custom metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary with enum handling"""
        data = asdict(self)
        data['status'] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'CheckpointState':
        """Create from dictionary"""
        # Handle status enum
        if 'status' in data and isinstance(data['status'], str):
            data['status'] = CheckpointStatus(data['status'])

        # Handle progress metrics
        if 'progress' in data and isinstance(data['progress'], dict):
            data['progress'] = ProgressMetrics(**data['progress'])

        return cls(**data)


class EnhancedCheckpoint:
    """Enhanced checkpoint with atomic updates and crash recovery"""

    def __init__(self, checkpoint_file: Path, auto_save_interval: int = 10):
        self.checkpoint_file = Path(checkpoint_file)
        self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

        self.backup_file = self.checkpoint_file.with_suffix('.checkpoint.backup.json')
        self.temp_file = self.checkpoint_file.with_suffix('.checkpoint.tmp.json')

        self.auto_save_interval = auto_save_interval
        self._save_counter = 0
        self._lock = threading.Lock()

        # Load or create state
        is_new = not self.checkpoint_file.exists()
        self.state = self._load_or_create_state()

        # Save new checkpoint immediately if it was just created
        if is_new:
            self.save(force=True)

    def _load_or_create_state(self) -> CheckpointState:
        """Load existing checkpoint or create new one with recovery"""
        # Try to load main checkpoint
        if self.checkpoint_file.exists():
            try:
                with open(self.checkpoint_file, encoding='utf-8') as f:
                    data = json.load(f)
                    state = CheckpointState.from_dict(data)

                    # Check if it was recovering
                    if state.status == CheckpointStatus.RUNNING:
                        logger.warning(f"Checkpoint {self.checkpoint_file} was running - possible crash detected")
                        state.status = CheckpointStatus.RECOVERING

                    logger.info(f"Loaded checkpoint: {self.checkpoint_file} (status: {state.status.value})")
                    return state
            except Exception as e:
                logger.error(f"Failed to load checkpoint {self.checkpoint_file}: {e}")

                # Try backup
                if self.backup_file.exists():
                    try:
                        logger.info(f"Attempting to restore from backup: {self.backup_file}")
                        shutil.copy(self.backup_file, self.checkpoint_file)

                        with open(self.checkpoint_file, encoding='utf-8') as f:
                            data = json.load(f)
                            state = CheckpointState.from_dict(data)
                            state.status = CheckpointStatus.RECOVERING
                            logger.info("Successfully restored from backup")
                            return state
                    except Exception as backup_error:
                        logger.error(f"Failed to restore from backup: {backup_error}")

        # Create new state
        logger.info(f"Creating new checkpoint: {self.checkpoint_file}")
        return CheckpointState(stage="unknown")

    def save(self, force: bool = False):
        """Save checkpoint with atomic write"""
        with self._lock:
            self._save_counter += 1

            # Auto-save logic
            if not force and self._save_counter % self.auto_save_interval != 0:
                return

            # Update timestamp
            self.state.updated_at = datetime.now().isoformat()

            try:
                # Write to temp file first
                with open(self.temp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.state.to_dict(), f, indent=2, ensure_ascii=False)

                # Create backup of current checkpoint
                if self.checkpoint_file.exists():
                    shutil.copy(self.checkpoint_file, self.backup_file)

                # Atomic rename
                shutil.move(str(self.temp_file), str(self.checkpoint_file))

                logger.debug(f"Checkpoint saved: {self.checkpoint_file}")
            except Exception as e:
                logger.error(f"Failed to save checkpoint {self.checkpoint_file}: {e}")

    def start(self, stage: str, total_items: int = 0, input_file: str | None = None):
        """Start checkpoint for a stage"""
        with self._lock:
            self.state.stage = stage
            self.state.status = CheckpointStatus.RUNNING
            self.state.progress.total_items = total_items
            self.state.progress.start_time = time.time()
            self.state.progress.last_update_time = time.time()

            if input_file:
                self.state.input_file = input_file
                self.state.input_file_hash = self._compute_file_hash(Path(input_file))

        self.save(force=True)
        logger.info(f"Started checkpoint for {stage} with {total_items} items")

    def update_progress(
        self,
        processed: int = 1,
        successful: int = 0,
        failed: int = 0,
        skipped: int = 0,
        last_item: str | None = None,
        index: int | None = None
    ):
        """Update progress metrics"""
        force_save = False

        with self._lock:
            self.state.progress.processed_items += processed
            self.state.progress.successful_items += successful
            self.state.progress.failed_items += failed
            self.state.progress.skipped_items += skipped
            self.state.progress.last_update_time = time.time()

            if last_item:
                self.state.last_processed_item = last_item

            if index is not None:
                self.state.last_processed_index = index
                # Force save when index changes for crash recovery
                force_save = True

            # Update estimated completion
            est = self.state.progress.estimate_completion()
            if est:
                self.state.progress.estimated_completion_time = est

        self.save(force=force_save)

    def complete(self):
        """Mark checkpoint as completed"""
        with self._lock:
            self.state.status = CheckpointStatus.COMPLETED
            self.state.progress.last_update_time = time.time()

        self.save(force=True)
        logger.info(f"Checkpoint completed: {self.state.stage}")

    def fail(self, error_message: str):
        """Mark checkpoint as failed"""
        with self._lock:
            self.state.status = CheckpointStatus.FAILED
            self.state.error_message = error_message
            self.state.error_count += 1
            self.state.last_error_time = datetime.now().isoformat()

        self.save(force=True)
        logger.error(f"Checkpoint failed: {self.state.stage} - {error_message}")

    def pause(self):
        """Pause checkpoint"""
        with self._lock:
            self.state.status = CheckpointStatus.PAUSED

        self.save(force=True)
        logger.info(f"Checkpoint paused: {self.state.stage}")

    def resume(self):
        """Resume from paused state"""
        with self._lock:
            if self.state.status in [CheckpointStatus.PAUSED, CheckpointStatus.RECOVERING]:
                self.state.status = CheckpointStatus.RUNNING
                self.state.progress.last_update_time = time.time()

        self.save(force=True)
        logger.info(f"Checkpoint resumed: {self.state.stage}")

    def should_skip(self, index: int) -> bool:
        """Check if item should be skipped (already processed)"""
        return index <= self.state.last_processed_index

    def get_resume_point(self) -> dict[str, Any]:
        """Get information for resuming"""
        with self._lock:
            return {
                'stage': self.state.stage,
                'status': self.state.status.value,
                'last_processed_index': self.state.last_processed_index,
                'last_processed_item': self.state.last_processed_item,
                'processed_items': self.state.progress.processed_items,
                'total_items': self.state.progress.total_items,
                'batch_id': self.state.batch_id
            }

    def get_progress_report(self) -> dict[str, Any]:
        """Get comprehensive progress report"""
        with self._lock:
            return {
                'stage': self.state.stage,
                'status': self.state.status.value,
                'progress_pct': self.state.progress.get_progress_percentage(),
                'success_rate': self.state.progress.get_success_rate(),
                'throughput': self.state.progress.get_throughput(),
                'processed': self.state.progress.processed_items,
                'total': self.state.progress.total_items,
                'successful': self.state.progress.successful_items,
                'failed': self.state.progress.failed_items,
                'skipped': self.state.progress.skipped_items,
                'eta_seconds': self.state.progress.estimated_completion_time,
                'elapsed_seconds': (time.time() - self.state.progress.start_time) if self.state.progress.start_time else 0
            }

    def validate_input_file(self, input_file: Path) -> tuple[bool, str]:
        """Validate input file hasn't changed"""
        if not self.state.input_file or not self.state.input_file_hash:
            return True, "No input file validation needed"

        if str(input_file) != self.state.input_file:
            return False, f"Input file mismatch: expected {self.state.input_file}, got {input_file}"

        if not input_file.exists():
            return False, f"Input file not found: {input_file}"

        current_hash = self._compute_file_hash(input_file)
        if current_hash != self.state.input_file_hash:
            return False, "Input file has changed (hash mismatch)"

        return True, "Input file validated"

    def is_stale(self, max_age_hours: int = 24) -> bool:
        """Check if checkpoint is stale"""
        try:
            updated_at = datetime.fromisoformat(self.state.updated_at)
            age = datetime.now() - updated_at
            return age > timedelta(hours=max_age_hours)
        except:
            return False

    @staticmethod
    def _compute_file_hash(file_path: Path, chunk_size: int = 8192) -> str:
        """Compute SHA256 hash of file"""
        sha256 = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except Exception as e:
            logger.error(f"Failed to compute file hash: {e}")
            return ""

    def reset(self):
        """Reset checkpoint to initial state, restoring pending count"""
        with self._lock:
            # Preserve total_items for requeue
            total_items = self.state.progress.total_items
            stage_name = self.state.stage

            # Create new state with INITIALIZED status
            self.state = CheckpointState(stage=stage_name)
            self.state.status = CheckpointStatus.INITIALIZED

            # Restore pending count (all items need to be reprocessed)
            self.state.progress.total_items = total_items
            self.state.progress.processed_items = 0
            self.state.progress.successful_items = 0
            self.state.progress.failed_items = 0
            self.state.progress.skipped_items = 0

        self.save(force=True)
        logger.info(f"Checkpoint reset: {self.state.stage} (pending items restored: {total_items})")


class UnifiedCheckpointManager:
    """Unified checkpoint manager for all pipeline stages"""

    def __init__(self, checkpoint_dir: Path):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._checkpoints: dict[str, EnhancedCheckpoint] = {}
        self._lock = threading.Lock()

    def get_checkpoint(self, stage: str, auto_create: bool = True) -> EnhancedCheckpoint | None:
        """Get or create checkpoint for a stage"""
        with self._lock:
            if stage not in self._checkpoints:
                if not auto_create:
                    return None

                checkpoint_file = self.checkpoint_dir / f"{stage}.checkpoint.json"
                self._checkpoints[stage] = EnhancedCheckpoint(checkpoint_file)

            return self._checkpoints[stage]

    def get_all_checkpoints(self) -> list[EnhancedCheckpoint]:
        """Get all active checkpoints"""
        with self._lock:
            # Also load any checkpoint files not yet in memory
            for checkpoint_file in self.checkpoint_dir.glob("*.checkpoint.json"):
                stage = checkpoint_file.stem.replace('.checkpoint', '')
                if stage not in self._checkpoints:
                    self._checkpoints[stage] = EnhancedCheckpoint(checkpoint_file)

            return list(self._checkpoints.values())

    def get_pipeline_progress(self) -> dict[str, Any]:
        """Get overall pipeline progress"""
        checkpoints = self.get_all_checkpoints()

        total_items = sum(cp.state.progress.total_items for cp in checkpoints)
        processed_items = sum(cp.state.progress.processed_items for cp in checkpoints)

        stages_status = {
            cp.state.stage: cp.state.status.value
            for cp in checkpoints
        }

        return {
            'total_items': total_items,
            'processed_items': processed_items,
            'progress_pct': (processed_items / total_items * 100) if total_items > 0 else 0,
            'stages': stages_status,
            'active_stages': sum(1 for cp in checkpoints if cp.state.status == CheckpointStatus.RUNNING),
            'completed_stages': sum(1 for cp in checkpoints if cp.state.status == CheckpointStatus.COMPLETED),
            'failed_stages': sum(1 for cp in checkpoints if cp.state.status == CheckpointStatus.FAILED)
        }

    def print_progress_report(self):
        """Print comprehensive progress report"""
        checkpoints = self.get_all_checkpoints()

        print("\n" + "=" * 80)
        print("Pipeline Progress Report")
        print("=" * 80)

        for checkpoint in checkpoints:
            report = checkpoint.get_progress_report()

            print(f"\nStage: {report['stage']}")
            print(f"Status: {report['status']}")
            print(f"Progress: {report['progress_pct']:.1f}% ({report['processed']}/{report['total']})")
            print(f"Success Rate: {report['success_rate']:.1f}%")
            print(f"Throughput: {report['throughput']:.1f} items/sec")

            if report['eta_seconds']:
                eta_mins = report['eta_seconds'] / 60
                print(f"ETA: {eta_mins:.1f} minutes")

            print(f"Successful: {report['successful']} | Failed: {report['failed']} | Skipped: {report['skipped']}")

        # Overall summary
        pipeline_progress = self.get_pipeline_progress()
        print(f"\nOverall Pipeline Progress: {pipeline_progress['progress_pct']:.1f}%")
        print(f"Active: {pipeline_progress['active_stages']} | Completed: {pipeline_progress['completed_stages']} | Failed: {pipeline_progress['failed_stages']}")
        print("=" * 80 + "\n")

    def cleanup_old_checkpoints(self, keep_days: int = 7):
        """Remove old checkpoint files"""
        cutoff_time = time.time() - (keep_days * 24 * 60 * 60)

        for checkpoint_file in self.checkpoint_dir.glob("*.checkpoint.json"):
            if checkpoint_file.stat().st_mtime < cutoff_time:
                try:
                    # Also remove backup
                    backup_file = checkpoint_file.with_suffix('.checkpoint.backup.json')
                    if backup_file.exists():
                        backup_file.unlink()

                    checkpoint_file.unlink()
                    logger.info(f"Cleaned up old checkpoint: {checkpoint_file}")
                except Exception as e:
                    logger.warning(f"Failed to cleanup {checkpoint_file}: {e}")

    def reset_all(self):
        """Reset all checkpoints"""
        for checkpoint in self._checkpoints.values():
            checkpoint.reset()

    def export_report(self, output_file: Path):
        """Export progress report to JSON file"""
        checkpoints = self.get_all_checkpoints()

        report = {
            'timestamp': datetime.now().isoformat(),
            'pipeline_progress': self.get_pipeline_progress(),
            'stages': [
                {
                    'stage': cp.state.stage,
                    'progress': cp.get_progress_report(),
                    'state': cp.state.to_dict()
                }
                for cp in checkpoints
            ]
        }

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"Progress report exported to: {output_file}")
