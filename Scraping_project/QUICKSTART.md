# Quick Start Guide - Fixed Pipeline

## What Was Fixed

### 1. Delta Lake WriterProperties Bug ✅
- **Fixed:** [src/common/delta_lake.py](src/common/delta_lake.py)
- Changed from dictionary to proper `WriterProperties` object
- Now writes to Delta Lake work correctly

### 2. Metrics Exporter Permission Error ✅
- **Fixed:** [monitoring/metrics_exporter.py](monitoring/metrics_exporter.py:140)
- Changed exports directory from `/exports` to `/app/exports`
- Container can now create the directory

### 3. Created Custom Reseed Script ✅
- **New File:** [reseed.py](reseed.py)
- Loads `uconn_urls.csv` into Delta Lake
- Validates the seed data
- Shows table statistics

---

## How to Use the Pipeline Now

### Step 1: Start the Infrastructure
```bash
# Start all services (Redis, Postgres, Kafka, Prometheus, Grafana, etc.)
python start.py --env local
```

This will start all containers but **NOT seed the URLs** yet.

### Step 2: Load Seed URLs into Delta Lake

**IMPORTANT:** You must load the seed URLs before the scrapers can work!

```bash
# Option A: Using the new reseed.py script (RECOMMENDED)
python reseed.py --force

# Option B: Using start.py with --reset-delta flag
# (This is what start.py does internally, but only if you pass the flag)
python start.py --env local --reset-delta
```

The reseed script will:
- Load all 143,208 URLs from `data/raw/uconn_urls.csv`
- Add URL hashes (SHA256)
- Add timestamps
- Validate the data was loaded correctly
- Show table statistics

**Expected Output:**
```
🚀 Delta Lake Reseed Script Starting...
Loading seed URLs from: /Users/.../uconn_urls.csv
Loaded 143208 URLs, 143208 unique URLs after deduplication
Seeding 143208 URLs into Delta Lake...
✅ Seed URLs loaded: 143208 records
✅ seed_urls table validated: 143208 records
🎉 Reseed Complete!
```

### Step 3: Restart the Scrapy App

The scrapy-app container was probably restarting because it found no URLs. Now that we've seeded them, restart it:

```bash
docker-compose restart scrapy-app
```

### Step 4: Restart Metrics Exporter (if it was failing)

```bash
docker-compose restart metrics-exporter
```

### Step 5: Monitor the Pipeline

```bash
# Check health of all Delta Lake tables
python cli.py health

# View scrapy logs
docker-compose logs -f scrapy-app

# View all logs
docker-compose logs -f

# Check specific service
docker-compose logs -f stage2-worker
```

### Step 6: Access Dashboards

- **Grafana:** http://localhost:3000
  - Username: `admin`
  - Password: `admin`

- **Prometheus A:** http://localhost:9091
- **Prometheus B:** http://localhost:9097

---

## Troubleshooting

### Scrapy App Still Restarting?

Check if seed URLs were loaded:
```bash
python cli.py health
```

You should see:
```
✓ seed_urls: 143208 rows, X files
```

If you see `0 rows`, run:
```bash
python reseed.py --force
docker-compose restart scrapy-app
```

### Metrics Exporter Still Failing?

Check if the fix was applied:
```bash
grep "/app/exports" monitoring/metrics_exporter.py
```

You should see:
```python
exports_dir = Path('/app/exports')
```

If not, the fix wasn't applied. Re-pull the code or manually edit the file.

### Stage2/Stage3 Workers Unhealthy?

This might be normal if they're waiting for work. Check logs:
```bash
docker-compose logs stage2-worker
docker-compose logs stage3-worker
```

### Need to Reset Everything?

```bash
# Stop all containers
docker-compose down

# Remove all volumes (WARNING: deletes all data!)
docker-compose down -v

# Start fresh
python start.py --env local
python reseed.py --force
```

---

## Advanced Usage

### Reseed Without Clearing (Just Refresh)

```bash
# This will overwrite the seed_urls table without deleting other tables
python reseed.py --force
```

### Reseed AND Clear All Tables

```bash
# WARNING: This deletes ALL Delta Lake tables!
python reseed.py --clear --force
```

### Custom CSV File

```bash
# Load from a different CSV file
python reseed.py --csv /path/to/custom_urls.csv --force
```

### Skip Validation (Faster)

```bash
# Skip validation step (not recommended for production)
python reseed.py --force --no-validate
```

---

## Validation Commands

### Check Table Statistics
```bash
python cli.py health
```

### Validate All Tables
```bash
python cli.py validate
```

### Export Data for Debugging
```bash
# Export specific table
python cli.py export --table seed_urls --output ./debug --format csv

# Export all tables
python cli.py export --output ./debug --format csv
```

### Count Records in Delta Lake
```python
# In Python
from src.common.delta_lake import get_delta_manager

manager = get_delta_manager()
count = manager.count('seed_urls')
print(f"Seed URLs: {count}")
```

---

## Files Modified

### Core Fixes
- [src/common/delta_lake.py](src/common/delta_lake.py) - Fixed WriterProperties
- [monitoring/metrics_exporter.py](monitoring/metrics_exporter.py) - Fixed exports path

### New Scripts
- [reseed.py](reseed.py) - Custom seed loading script
- [ISSUES_FOUND.md](ISSUES_FOUND.md) - Detailed issue tracking
- [QUICKSTART.md](QUICKSTART.md) - This file

---

## Why Was start.py "Ending Early"?

This is actually **by design**. The `start.py` script:

1. Starts docker-compose
2. Waits for postgres to be ready
3. **Only runs reset-delta if you pass the flag**
4. Exits (containers keep running in background)

The confusion was that `--reset-delta` is **optional** and **not the default**.

**Before:**
```bash
python start.py --env local
# Exits immediately, no seeding happens
```

**After (if you want seeding):**
```bash
python start.py --env local --reset-delta
# Exits after seeding the URLs
```

**Or use the new reseed.py:**
```bash
python start.py --env local
python reseed.py --force
# More explicit and gives better feedback
```

---

## Next Steps

1. Monitor the pipeline for a few minutes
2. Check Grafana dashboards
3. Verify data is being scraped:
   ```bash
   python cli.py health
   ```
4. Look for stage1_discovery records:
   ```bash
   python cli.py export --table stage1_discovery --output ./check --format csv
   ```

---

## Production Recommendations

1. **Use persistent volumes** for Delta Lake data
2. **Configure proper monitoring alerts** in Alertmanager
3. **Set up log aggregation** (ELK stack or similar)
4. **Regular backups** of Delta Lake tables
5. **Monitor disk space** (Delta Lake can grow quickly)
6. **Vacuum old data** periodically:
   ```bash
   python -c "from src.common.delta_lake import get_delta_manager; get_delta_manager().vacuum_all_tables(retention_hours=168)"
   ```

---

## Support

If you need help:
1. Check [ISSUES_FOUND.md](ISSUES_FOUND.md) for detailed troubleshooting
2. Review logs: `docker-compose logs [service-name]`
3. Validate Delta Lake: `python cli.py validate`
4. Check seed URLs count: `python reseed.py` (will show count without reseeding)
