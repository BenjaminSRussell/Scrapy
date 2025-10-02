# Checkpoint/Resume System

## Overview

The UConn Scraper features a **comprehensive checkpoint/resume system** that provides resilience across all pipeline stages. The system automatically saves progress, detects crashes, and enables seamless resumption from the last known good state.

## Key Features

- ✅ **Unified checkpoint management** across all stages
- ✅ **Automatic crash recovery** with state validation
- ✅ **Progress tracking** with ETA estimation
- ✅ **Atomic checkpoint updates** to prevent corruption
- ✅ **Input file validation** to detect data changes
- ✅ **Comprehensive progress reporting**
- ✅ **CLI tools** for checkpoint management

## Architecture

### Components

1. **EnhancedCheckpoint**: Core checkpoint with atomic updates and crash recovery
2. **UnifiedCheckpointManager**: Manages multiple stage checkpoints
3. **AsyncCheckpointTracker**: Checkpoint support for async processors
4. **CheckpointMiddleware**: Scrapy middleware for spider checkpoints
5. **CLI Tools**: Command-line checkpoint management

### Checkpoint State

Each checkpoint stores:
- **Progress metrics**: Total, processed, successful, failed, skipped
- **Resume information**: Last processed item/index, batch ID
- **Performance stats**: Throughput, ETA, success rate
- **Error tracking**: Error messages, counts, timestamps
- **Input validation**: File hash for detecting changes
- **Custom metadata**: Stage-specific information

## Usage

### Automatic Checkpointing

Checkpoints are **created automatically** during pipeline execution:

```bash
# Run pipeline - checkpoints created automatically
python -m src.orchestrator.main --env development --stage all
```

If the pipeline crashes or is interrupted:
- Current progress is saved to checkpoint
- State is marked as "recovering"
- Next run automatically resumes from checkpoint

### Manual Checkpoint Management

#### List Checkpoints

```bash
# Show all checkpoints
python tools/checkpoint_manager_cli.py list

# Output:
# Stage                          Status          Progress        Success Rate    Updated
# ================================================================================
# stage1_discovery               running         5000/10000 (50.0%)  98.5%      2025-10-02T12:30:45
# stage2_validation              completed       8000/8000 (100.0%)  96.2%      2025-10-02T12:25:30
# stage3_async_enrichment        paused          3000/8000 (37.5%)   94.1%      2025-10-02T12:28:15
```

#### Show Checkpoint Details

```bash
# Show detailed information for a stage
python tools/checkpoint_manager_cli.py show stage1_discovery

# Output includes:
# - Status and timestamps
# - Progress metrics
# - Performance statistics
# - Resume point information
# - Error details (if any)
# - Input file validation
```

#### Reset Checkpoints

```bash
# Reset specific checkpoint
python tools/checkpoint_manager_cli.py reset stage1_discovery

# Reset all checkpoints
python tools/checkpoint_manager_cli.py reset all --force

# Reset without confirmation
python tools/checkpoint_manager_cli.py reset stage2_validation --force
```

#### Export Progress Report

```bash
# Export detailed JSON report
python tools/checkpoint_manager_cli.py export --output progress_report.json

# View comprehensive report in terminal
python tools/checkpoint_manager_cli.py report
```

#### Cleanup Old Checkpoints

```bash
# Remove checkpoints older than 7 days
python tools/checkpoint_manager_cli.py cleanup --days 7
```

## Progress Tracking

### Metrics Tracked

- **Total items**: Expected number of items to process
- **Processed items**: Items completed so far
- **Successful items**: Items processed without errors
- **Failed items**: Items that encountered errors
- **Skipped items**: Items intentionally skipped

### Performance Metrics

- **Throughput**: Items processed per second
- **Success rate**: Percentage of successful items
- **ETA**: Estimated time to completion
- **Elapsed time**: Time since start

### Progress Reporting

Progress is logged automatically every 100 items:

```
[INFO] Progress: 45.5% (5000/11000) | Success: 98.2% | Throughput: 42.3 items/sec | ETA: 2.4 min
```

Detailed reports show:

```
================================================================================
Pipeline Progress Report
================================================================================

Stage: stage1_discovery
Status: running
Progress: 45.5% (5000/11000)
Success Rate: 98.2%
Throughput: 42.3 items/sec
ETA: 2.4 minutes
Successful: 4910 | Failed: 90 | Skipped: 0

Stage: stage2_validation
Status: completed
Progress: 100.0% (8000/8000)
Success Rate: 96.5%
Throughput: 35.2 items/sec
Successful: 7720 | Failed: 280 | Skipped: 0

Overall Pipeline Progress: 68.4%
Active: 1 | Completed: 1 | Failed: 0
================================================================================
```

## Crash Recovery

### Automatic Detection

The checkpoint system automatically detects crashes:

1. **Running checkpoint**: If a checkpoint is in "running" state at startup, crash is detected
2. **Status change**: Checkpoint status changes to "recovering"
3. **Resume point**: Pipeline resumes from last processed item/index

### Recovery Process

```
[WARNING] Detected incomplete checkpoint for stage1_discovery. Resuming from 5000 items
[INFO] Checkpoint status changed: running -> recovering
[INFO] Resuming from last processed index: 5000
[INFO] Skipping already processed items...
[INFO] Processing resumed
```

### Input File Validation

Before resuming, the system validates input files haven't changed:

```python
# Checkpoint stores input file hash
checkpoint.start("stage", total_items=1000, input_file="data/input.jsonl")

# On resume, validates file
is_valid, reason = checkpoint.validate_input_file(input_file)
if not is_valid:
    # Prevents resuming with changed data
    logger.error(f"Cannot resume: {reason}")
```

## Integration

### Async Processors (Stage 3)

The async enrichment processor has built-in checkpoint support:

```python
from src.stage3.async_enrichment import AsyncEnrichmentProcessor

async with AsyncEnrichmentProcessor(...) as processor:
    # Checkpoints created automatically
    await processor.process_urls(urls)

    # Progress tracked automatically
    # Resume supported automatically
```

**Features:**
- Automatic progress tracking
- Resume from last batch
- Crash recovery
- Progress reporting every 100 items

### Scrapy Spiders (Stages 1, 2)

Add checkpoint middleware to Scrapy settings:

```python
# In scrapy settings
DOWNLOADER_MIDDLEWARES = {
    'src.common.checkpoint_middleware.CheckpointMiddleware': 100,
}

# Checkpoint configuration
CHECKPOINT_ENABLED = True
CHECKPOINT_DIR = 'data/checkpoints'
CHECKPOINT_STAGE_NAME = 'stage1_discovery'
```

**Features:**
- Tracks requests and items
- Updates checkpoint on each item
- Handles spider close events (finished, shutdown, cancelled)
- Automatic resume on restart

### Manual Integration

For custom processors:

```python
from src.common.checkpoint_middleware import AsyncCheckpointTracker

async with AsyncCheckpointTracker(checkpoint_dir, "my_stage") as tracker:
    tracker.start(total_items=len(items))

    for i, item in enumerate(items):
        # Skip if already processed
        if tracker.should_skip(i):
            continue

        # Process item
        result = await process_item(item)

        # Update checkpoint
        tracker.update(
            processed=1,
            successful=1 if result.success else 0,
            failed=0 if result.success else 1,
            last_item=item.url,
            index=i
        )

        # Print progress every 100 items
        if i % 100 == 0:
            tracker.print_progress()
```

## Checkpoint File Format

Checkpoints are stored as JSON files:

```json
{
  "stage": "stage1_discovery",
  "status": "running",
  "progress": {
    "total_items": 10000,
    "processed_items": 5000,
    "successful_items": 4910,
    "failed_items": 90,
    "skipped_items": 0,
    "start_time": 1696258845.123,
    "last_update_time": 1696259045.456,
    "estimated_completion_time": 118.5
  },
  "last_processed_item": "https://uconn.edu/page5000",
  "last_processed_index": 5000,
  "batch_id": 5,
  "input_file": "data/processed/stage01/discovery_output.jsonl",
  "input_file_hash": "abc123...",
  "output_file": "data/processed/stage02/validation_output.jsonl",
  "created_at": "2025-10-02T12:00:00",
  "updated_at": "2025-10-02T12:30:45",
  "error_message": null,
  "error_count": 0,
  "metadata": {}
}
```

### Atomic Updates

Checkpoints use atomic write operations:

1. Write to temporary file: `.checkpoint.tmp.json`
2. Backup current checkpoint: `.checkpoint.backup.json`
3. Atomic rename temp to main: `checkpoint.json`

This ensures checkpoints are never corrupted, even if process crashes during save.

## Best Practices

### 1. Let Automatic Checkpoints Work

The system creates and manages checkpoints automatically. No manual intervention needed for normal operation.

### 2. Review Progress Regularly

Use the CLI to monitor long-running pipelines:

```bash
# Quick status check
python tools/checkpoint_manager_cli.py list

# Detailed view
python tools/checkpoint_manager_cli.py report
```

### 3. Reset When Needed

Reset checkpoints when:
- Starting fresh run with new data
- Input files have changed
- Checkpoint is corrupted or stale

```bash
python tools/checkpoint_manager_cli.py reset all --force
```

### 4. Clean Up Old Checkpoints

Prevent checkpoint directory bloat:

```bash
# Weekly cleanup
python tools/checkpoint_manager_cli.py cleanup --days 7
```

### 5. Export Reports for Analysis

Export checkpoint data for analysis:

```bash
python tools/checkpoint_manager_cli.py export --output reports/checkpoint_$(date +%Y%m%d).json
```

## Error Handling

### Failed Checkpoints

If a stage fails, checkpoint status is set to "failed":

```
[ERROR] Checkpoint stage1_discovery failed: Connection timeout
[ERROR] Error count: 1
[ERROR] Last error time: 2025-10-02T12:45:30
```

To recover:
1. Fix the underlying issue
2. Reset or resume the checkpoint
3. Re-run the pipeline

### Stale Checkpoints

Checkpoints older than 24 hours are considered stale:

```python
if checkpoint.is_stale(max_age_hours=24):
    logger.warning("Checkpoint is stale, consider resetting")
```

### Corrupted Checkpoints

If main checkpoint is corrupted, system automatically restores from backup:

```
[ERROR] Failed to load checkpoint: Invalid JSON
[INFO] Attempting to restore from backup
[INFO] Successfully restored from backup
```

## Checkpoint Status States

| Status | Description | Next Action |
|--------|-------------|-------------|
| `initialized` | Checkpoint created but not started | Start processing |
| `running` | Currently processing | Continue or detect crash on restart |
| `paused` | Intentionally paused | Resume processing |
| `completed` | Successfully finished | Archive or reset |
| `failed` | Encountered fatal error | Fix issue and retry |
| `recovering` | Detected crash, resuming | Automatic resume |

## Performance Impact

The checkpoint system is designed for minimal overhead:

- **Auto-save interval**: 10 updates (configurable)
- **Atomic writes**: ~1-2ms per save
- **Background saving**: Non-blocking
- **Typical overhead**: <1% of processing time

For a 10,000 item pipeline:
- Checkpoint saves: ~1,000 times
- Total checkpoint time: ~1-2 seconds
- Processing time: 3-5 minutes
- **Overhead: <1%**

## Troubleshooting

### Checkpoint Not Resuming

**Problem**: Pipeline starts from beginning despite checkpoint

**Solutions**:
1. Check checkpoint status: `python tools/checkpoint_manager_cli.py show <stage>`
2. Verify checkpoint isn't marked as "completed"
3. Ensure input file hasn't changed (hash validation)
4. Reset if needed: `python tools/checkpoint_manager_cli.py reset <stage>`

### Input File Changed Error

**Problem**: "Input file has changed since checkpoint"

**Solutions**:
1. If intentional: Reset checkpoint and restart
2. If not intentional: Restore original input file
3. To skip validation: Modify checkpoint to remove `input_file_hash`

### High Checkpoint Overhead

**Problem**: Checkpoint saves taking too long

**Solutions**:
1. Increase `auto_save_interval`:
   ```python
   checkpoint = EnhancedCheckpoint(file, auto_save_interval=50)  # Save every 50 updates
   ```
2. Use faster storage (SSD instead of HDD)
3. Reduce checkpoint metadata size

## Advanced Usage

### Custom Metadata

Store stage-specific information:

```python
checkpoint.state.metadata = {
    'spider_name': 'discovery',
    'max_depth': 5,
    'domains': ['uconn.edu'],
    'custom_config': {...}
}
checkpoint.save(force=True)
```

### Checkpoint Hooks

Execute code on checkpoint events:

```python
class CustomCheckpointTracker(AsyncCheckpointTracker):
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Custom cleanup
        if exc_type is None:
            await self.on_success()
        else:
            await self.on_failure(exc_val)

        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def on_success(self):
        # Send notification, upload results, etc.
        pass

    async def on_failure(self, error):
        # Alert, rollback, etc.
        pass
```

### Distributed Checkpoints

For distributed processing, use shared checkpoint storage:

```python
# Use network storage for checkpoints
checkpoint_dir = Path("/mnt/shared/checkpoints")
manager = UnifiedCheckpointManager(checkpoint_dir)

# Multiple workers can read/write checkpoints
# Implement locking for concurrent access
```

## Summary

The checkpoint/resume system provides:

- ✅ **Resilience**: Automatic crash recovery
- ✅ **Progress tracking**: Real-time metrics and ETA
- ✅ **Validation**: Input file integrity checks
- ✅ **Management**: CLI tools for inspection and control
- ✅ **Minimal overhead**: <1% performance impact
- ✅ **Atomic updates**: Corruption-proof saves
- ✅ **Unified interface**: Same API across all stages

**Result**: Long-running pipelines can be interrupted and resumed seamlessly, ensuring no work is lost and progress is always visible.
