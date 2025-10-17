# Scrapy Spider Diagnostic Tests

This directory contains 4 diagnostic test scripts to identify why the scout spiders are completing immediately without crawling.

## Problem Statement

The scout spiders are:
- Successfully loading 143,208 seed URLs from Delta Lake
- Completing immediately (in <1 second)
- Not generating any HTTP requests
- Not calling the parse() callback
- Resulting in 0 pages crawled

## Test Suite Overview

The tests isolate different components to identify the root cause:

### Test 1: Single Spider Instance (`test_single_spider.py`)
**Purpose**: Verify that a single scout spider can start without multiprocessing
**What it tests**: Whether the multiprocessing setup (`run_multiple_scouts.py`) is causing the issue
**Expected behavior**: Scout spider should crawl at least 10 pages

**Run**:
```bash
docker exec scraping_scrapy_app python test_single_spider.py
```

### Test 2: Verify start_requests() (`test_start_requests.py`)
**Purpose**: Check if `start_requests()` method is actually being called
**What it tests**: Whether Scrapy is invoking our custom `start_requests()` method
**Expected behavior**: Should see 🚀 messages when start_requests() is called, 📄 messages when pages are parsed

**Run**:
```bash
docker exec scraping_scrapy_app python test_start_requests.py
```

### Test 3: Minimal URLs Test (`test_minimal_urls.py`)
**Purpose**: Test with 5 hardcoded URLs, bypassing Delta Lake entirely
**What it tests**: Whether basic Scrapy crawling works, isolating Delta Lake/configuration issues
**Expected behavior**: All 5 URLs should be crawled successfully

**Run**:
```bash
docker exec scraping_scrapy_app python test_minimal_urls.py
```

### Test 4: Scout with Limited URLs (`test_scout_limited.py`)
**Purpose**: Test the actual scout spider with only 10 URLs from Delta Lake
**What it tests**: Whether the issue is related to the large number of URLs (143K)
**Expected behavior**: Scout spider should crawl the first 10 URLs from seed_urls table

**Run**:
```bash
docker exec scraping_scrapy_app python test_scout_limited.py
```

## Running All Tests

Use the test runner script to run all tests sequentially:

```bash
docker exec scraping_scrapy_app bash run_all_tests.sh
```

This will:
1. Run each test in order
2. Save output to individual log files (test1_output.log, test2_output.log, etc.)
3. Wait for user confirmation between tests
4. Provide analysis summary at the end

## Manual Test Execution

To run tests manually in Docker:

```bash
# Enter the container
docker exec -it scraping_scrapy_app bash

# Run individual tests
python test_single_spider.py
python test_start_requests.py
python test_minimal_urls.py
python test_scout_limited.py
```

## Interpreting Results

### If Test 1 fails:
- Issue is with the basic scout spider setup
- Check: Delta Lake connection, settings.py configuration

### If Test 2 fails:
- `start_requests()` is not being called by Scrapy
- Issue is with spider lifecycle or Scrapy configuration
- Check: Spider class inheritance, start_urls vs start_requests

### If Test 3 fails:
- Fundamental Scrapy setup issue
- Check: settings.py, middleware, downloader configuration
- Verify network connectivity from container

### If Test 4 fails but Test 3 passes:
- Issue specific to scout spider logic or Delta Lake integration
- Check: scout_spider.py parse method, URL processor, Redis connection

### If all tests pass:
- Issue is related to:
  - Large URL count (143K URLs overwhelming Scrapy)
  - Multiprocessing setup (8 concurrent instances)
  - Resource exhaustion (memory/file descriptors)

## Expected Output Format

Each test will print:
- 🕷️  Spider initialization messages
- 🚀 start_requests() call confirmation
- 📄 Page parsing messages
- ✅ Completion status
- ❌ Error messages (if any)

## Next Steps After Testing

Based on test results:

1. **Identify which test first fails** - This narrows down the component
2. **Compare passing vs failing tests** - Isolate the specific difference
3. **Review relevant logs** - Check Docker logs for the failing component
4. **Apply targeted fix** - Fix the specific component identified

## File Locations

- Test scripts: `/app/test_*.py`
- Test runner: `/app/run_all_tests.sh`
- Log outputs: `/app/test*_output.log` (after running)
- Scout spider: `/app/src/stage1/scout_spider.py`
- Base spider: `/app/src/stage1/base_spider.py`
- Settings: `/app/src/settings.py`

## Common Issues and Solutions

### Issue: "No module named 'src'"
**Solution**: Ensure `PYTHONPATH=/app` is set (already configured in Docker)

### Issue: "Delta Lake connection failed"
**Solution**: Verify `/app/data/delta_lake` directory exists and is writable

### Issue: "Redis connection refused"
**Solution**: Check that Redis container is running: `docker ps | grep redis`

### Issue: All tests timeout
**Solution**: Check network connectivity, increase timeout values in test scripts

## Debug Mode

To enable verbose Scrapy logging in any test, edit the test file and change:
```python
settings.set("LOG_LEVEL", "WARNING")  # Change to "DEBUG"
```

Then re-run the test to see detailed Scrapy internals.

## Contact

If tests reveal unexpected behavior, save the log files and share:
- Which test failed
- The test output log
- Docker Compose logs: `docker-compose logs scrapy-app`
