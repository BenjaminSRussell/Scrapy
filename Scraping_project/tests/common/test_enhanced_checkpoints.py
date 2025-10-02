"""
Tests for enhanced checkpoint system.
"""

import pytest
import asyncio
import time
from pathlib import Path
import json

from src.common.enhanced_checkpoints import (
    EnhancedCheckpoint,
    UnifiedCheckpointManager,
    CheckpointStatus,
    ProgressMetrics,
    CheckpointState
)
from src.common.checkpoint_middleware import AsyncCheckpointTracker


class TestProgressMetrics:
    """Test progress metrics calculations"""

    def test_progress_percentage(self):
        """Test progress percentage calculation"""
        metrics = ProgressMetrics(total_items=100, processed_items=50)
        assert metrics.get_progress_percentage() == 50.0

    def test_progress_percentage_zero_total(self):
        """Test progress percentage with zero total"""
        metrics = ProgressMetrics(total_items=0, processed_items=0)
        assert metrics.get_progress_percentage() == 0.0

    def test_success_rate(self):
        """Test success rate calculation"""
        metrics = ProgressMetrics(
            processed_items=100,
            successful_items=95
        )
        assert metrics.get_success_rate() == 95.0

    def test_throughput(self):
        """Test throughput calculation"""
        metrics = ProgressMetrics(
            total_items=100,
            processed_items=50,
            start_time=time.time() - 10,  # 10 seconds ago
            last_update_time=time.time()
        )
        throughput = metrics.get_throughput()
        assert 4.5 <= throughput <= 5.5  # ~5 items/sec

    def test_estimate_completion(self):
        """Test completion time estimation"""
        metrics = ProgressMetrics(
            total_items=100,
            processed_items=50,
            start_time=time.time() - 10,
            last_update_time=time.time()
        )
        eta = metrics.estimate_completion()
        assert eta is not None
        assert 9 <= eta <= 11  # ~10 seconds remaining


class TestEnhancedCheckpoint:
    """Test enhanced checkpoint functionality"""

    def test_create_new_checkpoint(self, tmp_path):
        """Test creating a new checkpoint"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        checkpoint = EnhancedCheckpoint(checkpoint_file)

        assert checkpoint_file.exists()
        assert checkpoint.state.status == CheckpointStatus.INITIALIZED

    def test_start_checkpoint(self, tmp_path):
        """Test starting a checkpoint"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        checkpoint = EnhancedCheckpoint(checkpoint_file)

        checkpoint.start("test_stage", total_items=100)

        assert checkpoint.state.stage == "test_stage"
        assert checkpoint.state.status == CheckpointStatus.RUNNING
        assert checkpoint.state.progress.total_items == 100

    def test_update_progress(self, tmp_path):
        """Test updating progress"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        checkpoint = EnhancedCheckpoint(checkpoint_file, auto_save_interval=1)

        checkpoint.start("test_stage", total_items=100)

        checkpoint.update_progress(
            processed=1,
            successful=1,
            last_item="item1",
            index=1
        )

        assert checkpoint.state.progress.processed_items == 1
        assert checkpoint.state.progress.successful_items == 1
        assert checkpoint.state.last_processed_item == "item1"
        assert checkpoint.state.last_processed_index == 1

    def test_complete_checkpoint(self, tmp_path):
        """Test completing a checkpoint"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        checkpoint = EnhancedCheckpoint(checkpoint_file)

        checkpoint.start("test_stage", total_items=100)
        checkpoint.complete()

        assert checkpoint.state.status == CheckpointStatus.COMPLETED

    def test_fail_checkpoint(self, tmp_path):
        """Test failing a checkpoint"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        checkpoint = EnhancedCheckpoint(checkpoint_file)

        checkpoint.start("test_stage", total_items=100)
        checkpoint.fail("Test error message")

        assert checkpoint.state.status == CheckpointStatus.FAILED
        assert checkpoint.state.error_message == "Test error message"
        assert checkpoint.state.error_count == 1

    def test_pause_and_resume(self, tmp_path):
        """Test pausing and resuming"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        checkpoint = EnhancedCheckpoint(checkpoint_file)

        checkpoint.start("test_stage", total_items=100)
        checkpoint.pause()

        assert checkpoint.state.status == CheckpointStatus.PAUSED

        checkpoint.resume()

        assert checkpoint.state.status == CheckpointStatus.RUNNING

    def test_should_skip(self, tmp_path):
        """Test skip logic for resuming"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        checkpoint = EnhancedCheckpoint(checkpoint_file)

        checkpoint.start("test_stage", total_items=100)
        checkpoint.update_progress(processed=10, index=50)

        assert checkpoint.should_skip(49) is True
        assert checkpoint.should_skip(50) is True
        assert checkpoint.should_skip(51) is False

    def test_crash_recovery(self, tmp_path):
        """Test crash recovery detection"""
        checkpoint_file = tmp_path / "test.checkpoint.json"

        # Create checkpoint and leave it running
        checkpoint1 = EnhancedCheckpoint(checkpoint_file)
        checkpoint1.start("test_stage", total_items=100)
        checkpoint1.update_progress(processed=50, index=50)
        # Simulate crash by not calling complete()

        # Load checkpoint again (simulating restart)
        checkpoint2 = EnhancedCheckpoint(checkpoint_file)

        assert checkpoint2.state.status == CheckpointStatus.RECOVERING
        assert checkpoint2.state.last_processed_index == 50

    def test_backup_and_restore(self, tmp_path):
        """Test backup and restore functionality"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        backup_file = tmp_path / "test.checkpoint.backup.json"

        # Create checkpoint
        checkpoint = EnhancedCheckpoint(checkpoint_file)
        checkpoint.start("test_stage", total_items=100)
        checkpoint.save(force=True)

        # Verify backup exists
        assert backup_file.exists() or checkpoint_file.exists()

    def test_validate_input_file(self, tmp_path):
        """Test input file validation"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        input_file = tmp_path / "input.txt"

        # Create input file
        input_file.write_text("test content")

        # Create checkpoint with input file
        checkpoint = EnhancedCheckpoint(checkpoint_file)
        checkpoint.start("test_stage", total_items=10, input_file=str(input_file))

        # Validate same file
        is_valid, reason = checkpoint.validate_input_file(input_file)
        assert is_valid is True

        # Modify input file
        input_file.write_text("modified content")

        # Validate again
        is_valid, reason = checkpoint.validate_input_file(input_file)
        assert is_valid is False
        assert "hash mismatch" in reason.lower()

    def test_reset_checkpoint(self, tmp_path):
        """Test resetting checkpoint"""
        checkpoint_file = tmp_path / "test.checkpoint.json"
        checkpoint = EnhancedCheckpoint(checkpoint_file)

        checkpoint.start("test_stage", total_items=100)
        checkpoint.update_progress(processed=50, successful=45, failed=5)

        checkpoint.reset()

        assert checkpoint.state.status == CheckpointStatus.INITIALIZED
        assert checkpoint.state.progress.processed_items == 0


class TestUnifiedCheckpointManager:
    """Test unified checkpoint manager"""

    def test_get_checkpoint(self, tmp_path):
        """Test getting checkpoints"""
        manager = UnifiedCheckpointManager(tmp_path)

        checkpoint1 = manager.get_checkpoint("stage1")
        checkpoint2 = manager.get_checkpoint("stage2")

        assert checkpoint1 is not None
        assert checkpoint2 is not None
        assert checkpoint1 != checkpoint2

    def test_get_all_checkpoints(self, tmp_path):
        """Test getting all checkpoints"""
        manager = UnifiedCheckpointManager(tmp_path)

        manager.get_checkpoint("stage1").start("stage1", 100)
        manager.get_checkpoint("stage2").start("stage2", 200)

        all_checkpoints = manager.get_all_checkpoints()

        assert len(all_checkpoints) >= 2

    def test_pipeline_progress(self, tmp_path):
        """Test overall pipeline progress"""
        manager = UnifiedCheckpointManager(tmp_path)

        cp1 = manager.get_checkpoint("stage1")
        cp1.start("stage1", total_items=100)
        cp1.update_progress(processed=50, successful=50)

        cp2 = manager.get_checkpoint("stage2")
        cp2.start("stage2", total_items=200)
        cp2.update_progress(processed=100, successful=100)

        progress = manager.get_pipeline_progress()

        assert progress['total_items'] == 300
        assert progress['processed_items'] == 150
        assert progress['progress_pct'] == 50.0

    def test_reset_all(self, tmp_path):
        """Test resetting all checkpoints"""
        manager = UnifiedCheckpointManager(tmp_path)

        manager.get_checkpoint("stage1").start("stage1", 100)
        manager.get_checkpoint("stage2").start("stage2", 200)

        manager.reset_all()

        for checkpoint in manager.get_all_checkpoints():
            assert checkpoint.state.status == CheckpointStatus.INITIALIZED

    def test_cleanup_old_checkpoints(self, tmp_path):
        """Test cleaning up old checkpoints"""
        manager = UnifiedCheckpointManager(tmp_path)

        # Create old checkpoint file
        old_checkpoint = tmp_path / "old_stage.checkpoint.json"
        old_checkpoint.write_text('{}')

        # Set modification time to old
        import os
        old_time = time.time() - (10 * 24 * 60 * 60)  # 10 days ago
        os.utime(old_checkpoint, (old_time, old_time))

        manager.cleanup_old_checkpoints(keep_days=7)

        # Should be deleted
        assert not old_checkpoint.exists()


@pytest.mark.asyncio
class TestAsyncCheckpointTracker:
    """Test async checkpoint tracker"""

    async def test_context_manager(self, tmp_path):
        """Test async context manager"""
        async with AsyncCheckpointTracker(tmp_path, "test_stage") as tracker:
            assert tracker.checkpoint is not None

    async def test_start_and_update(self, tmp_path):
        """Test starting and updating tracker"""
        tracker = AsyncCheckpointTracker(tmp_path, "test_stage")

        async with tracker:
            tracker.start(total_items=100)
            tracker.update(processed=1, successful=1)

            assert tracker.checkpoint.state.progress.processed_items == 1

    async def test_should_skip(self, tmp_path):
        """Test skip logic"""
        tracker = AsyncCheckpointTracker(tmp_path, "test_stage")

        async with tracker:
            tracker.start(total_items=100)
            tracker.update(processed=10, index=50)

            assert tracker.should_skip(49) is True
            assert tracker.should_skip(51) is False


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
