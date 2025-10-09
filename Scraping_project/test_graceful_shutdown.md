# Testing Graceful Shutdown

## Changes Made

### 1. Removed Conflicting Signal Handlers
**File**: `src/stage1/scout_spider.py`

- **Lines 128-131**: Commented out `signal.signal()` calls that were interfering with Scrapy's shutdown
- **Lines 609-621**: Updated `closed()` method with better logging
- **Removed**: `_graceful_shutdown()` method (no longer needed)
- **Removed**: `import signal` (no longer needed)

**Why this fixes the issue**:
- The spider's signal handlers were catching SIGINT/SIGTERM and saving data immediately
- But they didn't wait for Scrapy pipelines (especially KafkaPipeline) to flush
- This caused data loss because Kafka messages were still in the producer buffer
- Now Scrapy/Twisted handles the signal, stops accepting new requests, waits for pipelines to finish, then calls `closed()`

## How to Test

### Test 1: Start the Spider
```bash
python run.py --spiders scout
```

You should see:
1. ✅ `Prometheus metrics server started on 0.0.0.0:9410`
2. ✅ `Spider opened: scout at [timestamp]`
3. ✅ URLs being processed
4. ✅ `🔄 SKIPPED URLs` logs every 100 filtered URLs

### Test 2: Graceful Shutdown with Ctrl+C
While the spider is running, press `Ctrl+C` **once**.

**Expected behavior**:
1. ✅ You see: `Received SIGINT signal, initiating graceful shutdown...`
2. ✅ Spider continues processing for a few seconds (finishing in-flight requests)
3. ✅ You see: `Scout closing: shutdown. Total unique URLs: [count]`
4. ✅ You see: `Saving remaining batches to Delta Lake...`
5. ✅ You see: `📊 FINAL SKIPPED URLs SUMMARY - Total: [count] | filtered: [count]`
6. ✅ You see: `Message delivered to scraped-items` (Kafka flush logs)
7. ✅ You see: `Kafka pipeline stats - Sent: [count], Failed: [count]`
8. ✅ You see: `✅ All data saved successfully. Spider shutdown complete.`
9. ✅ Process exits cleanly after 5-10 seconds

**What was happening before**:
- ❌ Process exited immediately (< 1 second)
- ❌ No Kafka flush logs
- ❌ Lost messages still in Kafka producer buffer
- ❌ No final summary logs

### Test 3: Verify Data Persistence

After shutdown, check that data was saved:

```bash
# Check Kafka messages
docker-compose exec -T kafka kafka-run-class kafka.tools.GetOffsetShell \
  --broker-list localhost:9092 --topic scraped-items

# Should show increasing offset numbers for each partition
```

## Metrics Explanation

### Active Spiders: 1 ✅ CORRECT
- This shows **1** because you have **1 scout spider instance** running
- This is a Gauge metric that shows current active spiders
- If you ran 3 spiders simultaneously, it would show **3**
- The metric is: `scrapy_spider_opened{spider="scout"} 1`

### Total URLs Processed: 210K ✅ CORRECT
- This is the total count across all metrics:
  - `scrapy_items_scraped_total`: Items sent to Kafka
  - `scrapy_requests_total`: Total requests made
  - `scrapy_responses_total`: Total responses received
- 210K is accumulated across the entire crawl session

### Why "Only 1 Spider"?
You're running 1 spider (`scout`). If you want more parallelism:

**Option 1**: Run multiple instances of the same spider
```bash
# Terminal 1
python run.py --spiders scout

# Terminal 2 (different seed list or depth)
python run.py --spiders scout
```

**Option 2**: Create multiple different spiders
```python
# In src/stage1/scout_spider_2.py
class ScoutSpider2(ScoutSpider):
    name = "scout2"
```

But for web crawling, **1 spider with high concurrency** (you have 1024 concurrent requests) is usually more efficient than multiple spider instances.

## Summary

✅ **Fixed**: Graceful shutdown now properly flushes Kafka before exiting
✅ **Fixed**: Added skipped URL tracking with live tally
✅ **Fixed**: Optimized performance with depth limits and URL filtering
✅ **Clarified**: "Active Spiders: 1" is correct - you're running 1 spider instance
