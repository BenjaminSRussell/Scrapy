# ScoutSpider Refactoring Summary

## What Changed

### File: `src/stage1/scout_spider.py`

**Before:**
- Simple subclass of BaseSpider
- Inherited parse() method that processed everything
- Mixed responsibilities (discovery + processing)

**After:**
- Still inherits from BaseSpider (reuses core functionality)
- **Overrides `parse()` method** with dual-queueing strategy:
  1. **HTML pages** → Queue for JavaScriptSpider + Stage 2
  2. **Non-HTML pages** (PDFs, DOCs) → Queue for Stage 2 only
  3. **Static assets** (images, CSS, JS) → Discard immediately
  4. **Offsite links** → Log but don't follow

## Key Features

### 1. Dual-Queueing Strategy
```python
if content_hint == 'html':
    # HTML page - queue for BOTH JavaScriptSpider AND Stage 2
    yield self._queue_for_javascript_spider(url, response.url)
    yield self._queue_for_stage2(url, response.url, content_hint)
else:
    # Non-HTML page - queue for Stage 2 only
    yield self._queue_for_stage2(url, response.url, content_hint)
```

### 2. Inherits from BaseSpider
- ✅ `_extract_urls()` - URL extraction
- ✅ `_hash_url()` - URL hashing for dedup
- ✅ `_is_external_url()` - Domain filtering (uconn.edu)
- ✅ `_create_offsite_item()` - Offsite link logging
- ✅ `_categorize_skip_reason()` - Resource categorization
- ✅ `_track_skip()` - Metrics tracking

### 3. New Scout-Specific Methods
- `_is_static_asset()` - Check if URL is static resource
- `_guess_content_type()` - Guess content from URL extension
- `_queue_for_javascript_spider()` - Create JS queue item
- `_queue_for_stage2()` - Create Stage 2 queue item
- `_log_scout_stats()` - Scout-specific metrics

### 4. Performance Tracking
```python
self.scout_stats = {
    'html_queued_js': 0,
    'pages_queued_stage2': 0,
    'static_discarded': 0,
}
```

## Queue Item Schemas

### JavaScript Spider Queue
```python
{
    'url': 'https://uconn.edu/page',
    'parent_url': 'https://uconn.edu',
    'priority': 1,
    'status': 'pending',
    'queued_at': '2025-10-13T12:00:00',
    'queued_by': 'scout',
    'target_spider': 'javascript',
}
```

### Stage 2 Queue
```python
{
    'url': 'https://uconn.edu/page',
    'parent_url': 'https://uconn.edu',
    'content_hint': 'html',  # or 'pdf', 'doc', 'media'
    'priority': 2,  # 2 for HTML, 1 for others
    'status': 'pending',
    'queued_at': '2025-10-13T12:00:00',
    'queued_by': 'scout',
    'target_stage': 'stage2',
}
```

## Static Assets Discarded

Scout immediately discards these file types:
- Images: `.jpg`, `.png`, `.gif`, `.svg`, `.webp`, etc.
- Stylesheets/Scripts: `.css`, `.js`, `.map`
- Media: `.mp4`, `.mp3`, `.avi`, `.mov`, `.wav`
- Archives: `.zip`, `.rar`, `.7z`, `.tar`, `.gz`
- Fonts: `.woff`, `.woff2`, `.ttf`, `.eot`, `.otf`
- Executables: `.exe`, `.dmg`, `.pkg`, `.deb`, `.rpm`

## Next Steps Required

### 1. Create JavaScriptSpider ⏳
- File: `src/stage1/javascript_spider.py`
- Read from `js_spider_queue` table
- Use scrapy-playwright for rendering
- Extract URLs from rendered HTML
- Update queue status to 'completed'

### 2. Update Delta Lake Tables ⏳
Add to `src/common/delta_lake.py`:
```python
self.tables = {
    # ... existing tables ...
    'js_spider_queue': self.base_path / 'js_spider_queue',
    'stage2_queue': self.base_path / 'stage2_queue',
}
```

### 3. Update Pipeline ⏳
File: `src/pipelines.py`

Handle new queue items:
- Items with `target_spider='javascript'` → Write to `js_spider_queue`
- Items with `target_stage='stage2'` → Write to `stage2_queue`

### 4. Update Stage 2 Worker ⏳
File: `src/stage2/stage2_worker.py`

Read from `stage2_queue` instead of `stage1_discovery`:
```python
# OLD
queue_items = delta.read('stage1_discovery')

# NEW
queue_items = delta.read('stage2_queue', filters={'status': 'pending'})
```

### 5. Delete Obsolete Files ⏳
```bash
rm src/stage1/js_bot.py
rm src/stage1/js_detection.py
```

## Testing Checklist

- [ ] Scout processes only uconn.edu domains
- [ ] Scout discards static assets
- [ ] Scout logs offsite links
- [ ] Scout queues HTML for JS + Stage 2
- [ ] Scout queues PDFs for Stage 2 only
- [ ] Scout discovery rate >100 URLs/sec
- [ ] Queue items have correct schema
- [ ] BaseSpider methods still work

## Deployment Steps

1. **Backup current data**
   ```bash
   docker-compose exec scrapy-app tar -czf /tmp/backup.tar.gz /app/data
   ```

2. **Deploy new code**
   ```bash
   git pull
   docker-compose build
   docker-compose up -d
   ```

3. **Verify metrics**
   ```bash
   docker-compose logs -f scrapy-app | grep SCOUT
   ```

4. **Check queue tables**
   ```bash
   # Verify new tables exist
   ls /app/data/delta_lake/
   ```

---

**Status:** ✅ Scout refactored, inherits from BaseSpider
**Date:** 2025-10-13
**Next:** Create JavaScriptSpider
