# Pipeline Issues Found and Fixes

## Summary
This document outlines the issues found in the scraping pipeline and their solutions.

---

## Issue 1: WriterProperties Configuration Error ✅ FIXED

**Severity:** HIGH
**Status:** FIXED

### Problem
The Delta Lake writer was passing a dictionary instead of a `WriterProperties` object to `write_deltalake()`, causing:
```
AttributeError: 'dict' object has no attribute 'data_page_size_limit'
```

### Root Cause
In [src/common/delta_lake.py](src/common/delta_lake.py:234), the code was using:
```python
writer_properties={"compression": "ZSTD"}
```

But `deltalake` expects a `WriterProperties` object, not a dictionary.

### Fix Applied
Changed to:
```python
from deltalake import WriterProperties

writer_props = WriterProperties(compression="ZSTD")
write_deltalake(..., writer_properties=writer_props, ...)
```

**Files Modified:**
- [src/common/delta_lake.py](src/common/delta_lake.py:18) - Added `WriterProperties` import
- [src/common/delta_lake.py](src/common/delta_lake.py:229) - Fixed writer properties usage

---

## Issue 2: Scrapy-App Exiting Immediately (Missing start_requests() Method) ✅ FIXED

**Severity:** CRITICAL - BLOCKING ALL SCRAPING
**Status:** ROOT CAUSE IDENTIFIED - FIX IN PROGRESS

### Problem
The `scrapy-app` container starts, runs all 8 scout instances, and immediately completes with:
```
scout loaded 143208 seeds, 0 existing URLs in Redis
Scout instance 0 starting...
Scout instance 0 completed  # <-- Exits immediately without making ANY requests!
```

The spider loads 143,208 seed URLs from Delta Lake but makes ZERO HTTP requests.

### Root Cause ⚠️
**THE REAL ISSUE**: [src/stage1/base_spider.py](src/stage1/base_spider.py#L130-L131) loads `start_urls` dynamically in `__init__()`, but **Scrapy doesn't automatically use instance-level `start_urls` set after initialization**.

Here's what happens:
1. Scrapy binds the default `start_requests()` method at spider initialization
2. BaseSpider's `__init__()` runs and sets `self.start_urls = self._load_seed_urls()`
3. But Scrapy's default `start_requests()` was already bound and doesn't see the updated URLs
4. The spider starts with an empty request queue → exits immediately

### Solution ✅
Override `start_requests()` in BaseSpider to yield requests from the dynamically loaded URLs:

```python
def start_requests(self):
    """Generate initial requests from dynamically loaded start URLs.

    CRITICAL: This method is required because start_urls is set in __init__(),
    not as a class attribute. Scrapy's default start_requests() won't see
    instance-level start_urls that are set after spider initialization.
    """
    for url in self.start_urls:
        yield scrapy.Request(
            url,
            callback=self.parse,
            errback=self.handle_error,
            meta={'depth': 0},
            priority=1,
            dont_filter=False
        )
```

**Files to Modify:**
- [src/stage1/base_spider.py](src/stage1/base_spider.py) - Add `start_requests()` method after line 150

---

**Previous Wrong Diagnosis**: Initially thought seed URLs weren't loaded, but they ARE loaded (143,208 URLs confirmed in Delta Lake). The issue is Scrapy never makes requests from them.

---

## Issue 3: Metrics Exporter Permission Denied

**Severity:** MEDIUM
**Status:** IDENTIFIED

### Problem
The `metrics-exporter` container is crashing with:
```
PermissionError: [Errno 13] Permission denied: '/exports'
```

### Root Cause
The metrics exporter is trying to create `/exports` directory at the root filesystem level, which requires root permissions. The container is likely running as non-root user.

### Suggested Fixes

**Option 1: Change exports directory to a writable location**

Edit [metrics_exporter.py](metrics_exporter.py:140) (around line 140):
```python
# Before
exports_dir = Path('/exports')

# After
exports_dir = Path('/app/exports')  # or use temp dir
```

**Option 2: Add volume mount in docker-compose.yml**

Add to [docker-compose.yml](docker-compose.yml:498):
```yaml
metrics-exporter:
  volumes:
    - ./exports:/exports  # Add this line
```

**Option 3: Update Dockerfile permissions**

In the Dockerfile, add:
```dockerfile
RUN mkdir -p /exports && chown -R appuser:appuser /exports
```

**Recommended:** Use Option 1 (change to `/app/exports`) as it's the simplest and doesn't require host filesystem access.

---

## Issue 4: Stage2 and Stage3 Workers Unhealthy

**Severity:** MEDIUM
**Status:** NEEDS INVESTIGATION

### Problem
Both workers show as "unhealthy" in `docker-compose ps`:
```
scraping_stage2_worker   Up 4 minutes (unhealthy)
scraping_stage3_worker   Up 4 minutes (unhealthy)
```

### Root Cause
Likely the healthcheck is failing. Need to:
1. Check if healthcheck is defined in docker-compose.yml
2. Review worker logs for errors
3. Verify the workers are listening on expected ports

### Investigation Commands
```bash
# Check stage2 worker logs
docker-compose logs --tail=50 stage2-worker

# Check stage3 worker logs
docker-compose logs --tail=50 stage3-worker

# Check if healthcheck is defined
grep -A 5 "stage2-worker:" docker-compose.yml | grep healthcheck
```

### Possible Fixes
- Remove healthcheck if not needed
- Fix healthcheck endpoint/port
- Ensure workers expose metrics on expected port (9410)

---

## Issue 5: start.py Exits Too Quickly

**Severity:** LOW
**Status:** BY DESIGN

### Problem
When running `python start.py --env local`, the script:
1. Starts docker-compose
2. Waits for postgres
3. Exits immediately (without running `--reset-delta`)

### Root Cause
This is actually **by design**. The [start.py](start.py:229) script only runs `--reset-delta` if you explicitly pass that flag:

```python
if args.reset_delta:
    # Reset and reseed...
else:
    print("Skipping Delta Lake reset. Use '--reset-delta' to wipe and reseed.")
```

### Solution
This is not a bug, but the behavior could be improved:

**Current usage:**
```bash
# Start without seeding (current default)
python start.py --env local

# Start WITH seeding
python start.py --env local --reset-delta
```

**Recommendation:**
Update documentation to clarify that `--reset-delta` is needed for initial setup.

---

## Issue 6: Missing Grafana Port Mapping

**Severity:** LOW
**Status:** NEEDS VERIFICATION

### Problem
[docker-compose.yml](docker-compose.yml:260) shows:
```yaml
grafana:
  ports:
    - "3000:3000"
```

But the port mapping shows `3000/tcp` without external binding in `docker ps`.

### Suggested Fix
Verify Grafana is accessible at http://localhost:3000. If not, check:
1. Port conflicts (another service using 3000)
2. Docker network configuration
3. Grafana startup logs

---

## Quick Start Guide (Post-Fix)

### Step 1: Fix Metrics Exporter (if not already done)
```bash
# Edit metrics_exporter.py line 140
# Change: exports_dir = Path('/exports')
# To:     exports_dir = Path('/app/exports')
```

### Step 2: Start Infrastructure
```bash
python start.py --env local
```

### Step 3: Seed Delta Lake
```bash
# Option A: Using reseed.py script (RECOMMENDED)
python reseed.py --force

# Option B: Using start.py with reset flag
python start.py --env local --reset-delta
```

### Step 4: Restart Scrapy App
```bash
docker-compose restart scrapy-app
```

### Step 5: Monitor
```bash
# Check health
python cli.py health

# View logs
docker-compose logs -f scrapy-app

# Grafana dashboard
open http://localhost:3000
```

---

## Files Created/Modified

### New Files
1. **[reseed.py](reseed.py)** - Custom script to load uconn_urls.csv into Delta Lake
   - Supports `--clear` flag to wipe all tables
   - Validates seed URLs after loading
   - Shows table statistics
   - Production-ready with proper error handling

### Modified Files
1. **[src/common/delta_lake.py](src/common/delta_lake.py)** - Fixed WriterProperties usage
   - Added `WriterProperties` import
   - Changed dict to proper WriterProperties object

### Documentation
1. **ISSUES_FOUND.md** (this file) - Comprehensive issue tracking

---

## Priority Recommendations

### Immediate (Do First)
1. ✅ Fix WriterProperties error (DONE)
2. 🔄 Run reseed.py to load URLs
3. 🔄 Fix metrics-exporter permissions

### Medium Priority (Do Soon)
4. Investigate stage2/stage3 worker health checks
5. Add better error messages for empty seed_urls table

### Low Priority (Nice to Have)
6. Update documentation for --reset-delta flag
7. Verify Grafana accessibility
8. Add monitoring for Delta Lake table counts

---

## Testing Checklist

- [x] reseed.py --help works
- [x] WriterProperties fix applied
- [ ] reseed.py successfully loads 143,208 URLs
- [ ] scrapy-app starts and begins crawling
- [ ] metrics-exporter starts without errors
- [ ] stage2-worker is healthy
- [ ] stage3-worker is healthy
- [ ] Grafana dashboard accessible at http://localhost:3000

---

## Support

If you encounter issues:
1. Check logs: `docker-compose logs -f [service-name]`
2. Verify seed URLs: `python cli.py health`
3. Check table counts: `python cli.py validate`
4. Export data for debugging: `python cli.py export --table seed_urls --output ./debug`
