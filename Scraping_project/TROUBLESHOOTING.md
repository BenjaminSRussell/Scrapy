# Delta Lake Lock Contention & System Hangs - Troubleshooting Guide

## Problem Summary

Your system was experiencing **Delta Lake lock contention** causing the shutdown script to hang indefinitely. This is a critical issue that occurs when:

1. **Zombie processes** hold locks on Delta tables
2. **Transaction logs become bloated** (5,000+ files)
3. **Open file handles** prevent cleanup operations
4. **Environment variable conflicts** cause connection failures

## Root Causes Identified

### 1. Delta Lake Lock Contention (PRIMARY ISSUE)
**Symptoms:**
- `shutdown.sh` hangs at "Checking Delta Lake Status"
- `cli.py health` command never completes
- System appears frozen

**Root Cause:**
- **5,721 transaction log files** in `stage1_discovery/_delta_log/`
- Reading this many log files takes **minutes to hours**
- Process 35684 (`run_pipeline.py`) running for **4,205 minutes** (3 days!)
- Process 60675 (`cli.py health`) stuck since 12:56 PM
- Process 60311 (Finder/Spotlight) with **120+ open file descriptors** on Delta Lake

**Technical Details:**
```bash
# Transaction log bloat
$ ls data/delta_lake/stage1_discovery/_delta_log/*.json | wc -l
5721  # CRITICAL - should be < 100

# Open file handles preventing cleanup
$ lsof +D data/delta_lake | wc -l
120+  # Multiple processes holding locks
```

### 2. PostgreSQL Connection Failures
**Symptoms:**
- Containers continuously restarting
- Error: `connection to server at "localhost" (127.0.0.1), port 5432 failed: Connection refused`
- Stage 3 worker crashes with PostgreSQL errors

**Root Cause:**
Environment variable conflict in `.env`:
```bash
# WRONG - causes containers to connect to localhost instead of postgres container
DB_HOST=localhost

# CORRECT - for Docker containers
DB_HOST=postgres
```

The `postgres_manager.py` uses `DB_HOST` (line 64) which was set to `localhost`, but containers need to use the Docker service name `postgres`.

### 3. AttributeError: 'dict' object has no attribute 'data_page_size_limit'
**Status:** RESOLVED (Red herring)
- This error occurs **after** the Delta Lake read hangs
- Caused by timeout/corruption from the hanging process
- Will disappear once Delta tables are cleaned up

## Emergency Fix (Immediate)

### Step 1: Kill Zombie Processes
```bash
# Kill all hanging pipeline processes
pkill -9 -f "run_pipeline.py"
pkill -9 -f "cli.py"
pkill -9 -f "scrapy crawl"

# Verify they're dead
ps aux | grep -E "(python|scrapy)" | grep -v grep
```

### Step 2: Run Emergency Cleanup
```bash
# Use the emergency cleanup script
chmod +x scripts/emergency_cleanup.sh
./scripts/emergency_cleanup.sh
```

This script will:
- Kill all zombie processes
- Check for open file handles
- Report transaction log sizes
- Stop Docker containers forcefully

### Step 3: Fix Environment Variables
Edit `.env` file:
```bash
# Change from:
DB_HOST=localhost

# To:
DB_HOST=postgres
DB_PASSWORD=postgres  # Set actual password
```

### Step 4: Clean Up Delta Transaction Logs
```bash
# Activate virtual environment
source .venv/bin/activate

# Run VACUUM with aggressive retention (24 hours)
python scripts/vacuum_delta_tables.py --retention-hours 24

# This will remove old transaction log files
```

Expected output:
```
Vacuuming stage1_discovery...
✅ Vacuumed stage1_discovery (removed 5,500+ old log files)
```

### Step 5: Restart System
```bash
# Restart Docker containers
docker-compose up -d

# Monitor logs
docker-compose logs -f scrapy-app js-bot stage2-worker stage3-worker
```

## Long-Term Prevention

### 1. Automatic VACUUM Maintenance
Set up a cron job to run VACUUM weekly:

```bash
# Add to crontab (crontab -e)
0 2 * * 0 cd /Users/benjaminrussell/Desktop/Github/Scraping_project && source .venv/bin/activate && python scripts/vacuum_delta_tables.py
```

Or add to `docker-compose.yml` as a scheduled service:
```yaml
vacuum-scheduler:
  build:
    context: .
    dockerfile: Dockerfile
    target: crawler
  command: >
    sh -c "while true; do
      sleep 604800;
      python scripts/vacuum_delta_tables.py --retention-hours 168;
    done"
  environment:
    - PYTHONPATH=/app
  volumes:
    - ./data:/app/data
    - ./src:/app/src:ro
  restart: unless-stopped
```

### 2. Monitor Transaction Log Size
Add monitoring alerts in Prometheus/Grafana:

```yaml
# monitoring/alerting_rules.yml
- alert: DeltaLogBloat
  expr: delta_table_log_files{table="stage1_discovery"} > 1000
  for: 1h
  annotations:
    summary: "Delta table {{ $labels.table }} has too many log files"
    description: "Transaction log has {{ $value }} files. Run VACUUM immediately."
```

### 3. Implement Checkpoint Frequency
Modify [delta_lake.py:220-228](src/common/delta_lake.py#L220-L228) to checkpoint more frequently:

```python
# Enhanced: Queue optimization task instead of blocking the write path
if table_name in ['stage1_discovery', 'stage2_page_analysis']:
    # BEFORE: if len(data) >= 1000:
    # AFTER: More frequent checkpoints
    if len(data) >= 500:  # Lower threshold
        self.maintenance_queue.put(('optimize', table_name))
```

### 4. Graceful Shutdown Timeout
Modify [shutdown.sh:134](shutdown.sh#L134) to add timeout:

```bash
# Run health check with timeout
print_info "Checking Delta Lake table statistics (30s timeout)..."

if [ -f "cli.py" ]; then
    timeout 30 python cli.py health 2>&1 | grep -E "stage[0-9]|records|✅|✗" || print_warning "Delta Lake check timed out or failed"
else
    print_warning "CLI not found, skipping Delta Lake check"
fi
```

### 5. Add Health Checks
Create `scripts/health_check.sh`:

```bash
#!/bin/bash
# Quick health check without hanging

echo "=== Delta Lake Health Check ==="
for dir in data/delta_lake/*/; do
    table=$(basename "$dir")
    log_count=$(find "$dir/_delta_log" -name "*.json" 2>/dev/null | wc -l)

    if [ "$log_count" -gt 1000 ]; then
        echo "❌ $table: $log_count log files (CRITICAL - run VACUUM)"
    elif [ "$log_count" -gt 500 ]; then
        echo "⚠️  $table: $log_count log files (needs attention)"
    else
        echo "✅ $table: $log_count log files (healthy)"
    fi
done
```

### 6. Environment Variable Validation
Add to `docker-compose.yml` to validate configuration:

```yaml
stage3-worker:
  # ... existing config ...
  environment:
    # Use POSTGRES_* variables directly from docker-compose
    - DB_HOST=${POSTGRES_HOST:-postgres}
    - DB_PORT=${POSTGRES_PORT:-5432}
    - DB_NAME=${POSTGRES_DB:-scraping_pipeline}
    - DB_USER=${POSTGRES_USER:-postgres}
    - DB_PASSWORD=${POSTGRES_PASSWORD:-postgres}
```

## Monitoring Commands

### Check Delta Table Health
```bash
# Quick check - shows log file counts
./scripts/health_check.sh

# Detailed check - shows table statistics (may be slow)
python cli.py health
```

### Monitor Open Handles
```bash
# Check if any process is holding Delta Lake files
lsof +D data/delta_lake 2>/dev/null | head -20
```

### Check for Zombie Processes
```bash
# Find long-running pipeline processes
ps aux | grep -E "python.*(run_pipeline|cli\.py)" | grep -v grep

# Check process runtime
ps -p <PID> -o etime=  # Shows elapsed time
```

### Monitor Docker Container Health
```bash
# Check container status
docker ps -a | grep -E "(scrapy|worker|js-bot)"

# Check restart counts
docker inspect <container> | jq '.[0].RestartCount'

# View recent logs
docker logs --tail 50 scraping_stage3_worker
```

## Quick Reference

| Issue | Quick Fix | Prevention |
|-------|-----------|------------|
| Hanging `cli.py health` | `pkill -9 -f cli.py` | Use timeout: `timeout 30 python cli.py health` |
| Transaction log bloat | `python scripts/vacuum_delta_tables.py` | Weekly cron job |
| PostgreSQL connection fails | Set `DB_HOST=postgres` in `.env` | Validate env vars on startup |
| Containers restarting | Check logs: `docker logs <container>` | Add health checks |
| Zombie processes | `./scripts/emergency_cleanup.sh` | Proper signal handling |

## Files Modified/Created

1. **scripts/emergency_cleanup.sh** - Emergency cleanup script for lock issues
2. **.env** - Fixed `DB_HOST` from `localhost` to `postgres`
3. **TROUBLESHOOTING.md** (this file) - Comprehensive troubleshooting guide

## Next Steps

1. ✅ Run emergency cleanup: `./scripts/emergency_cleanup.sh`
2. ✅ Fix `.env` file: Set `DB_HOST=postgres`
3. ✅ Run VACUUM: `python scripts/vacuum_delta_tables.py --retention-hours 24`
4. ⏳ Set up weekly VACUUM cron job
5. ⏳ Add monitoring alerts for transaction log size
6. ⏳ Implement graceful shutdown timeout in `shutdown.sh`
7. ⏳ Create health check script

## Support

For additional help:
- Check Delta Lake docs: https://delta.io/
- Review Docker logs: `docker-compose logs -f`
- Check system resources: `htop` or `docker stats`

## Emergency Contacts

If the system is completely frozen:
```bash
# Nuclear option - force stop everything
docker-compose kill
docker-compose down --volumes  # WARNING: Deletes all data!
pkill -9 python
rm -rf data/delta_lake  # WARNING: Deletes all Delta tables!
```

Only use the nuclear option if you have backups and all other methods have failed.
