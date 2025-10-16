# Quick Start: Multi-Tiered Spider System

## What Was Built

A sophisticated 3-tier web crawling system with intelligent URL routing:

1. **Scout Spider** - Fast URL discovery (existing, already working)
2. **JS Spider (Enhanced)** - Priority queue + aggressive async rendering
3. **Depth Spider (Enhanced)** - Hidden URL extraction + value assessment

## Key Enhancements

### 1. JS Priority Queue (`src/common/js_priority_queue.py`)
- Redis sorted sets for priority-based scheduling
- Atomic operations (thread-safe)
- Priority levels: 0-100 (Critical → Low)
- Batch operations for high throughput

### 2. URL Value Assessor (`src/common/url_value_assessor.py`)
- Intelligent URL scoring (0-100)
- Pattern-based value assessment
- Spider routing recommendations
- Content likelihood estimation

### 3. Hidden URL Extractor (`src/common/hidden_url_extractor.py`)
- Data attribute extraction (data-url, data-src, etc.)
- JSON-LD structured data parsing
- JavaScript code analysis for API endpoints
- iframe source extraction
- Meta refresh detection
- Sitemap discovery

### 4. Enhanced Depth Spider (`src/stage1/deep_dive_spider.py`)
- Integrated hidden URL extraction
- URL value assessment
- Smart routing to JS spider
- Comprehensive stats tracking

### 5. Enhanced JS Spider (`src/stage1/js_spider.py`)
- Priority queue integration
- Increased concurrency (10 → 20 requests)
- Priority-based request ordering
- Batch dequeuing
- Enhanced memory limits (8GB → 12GB)

### 6. Monitoring Optimizations
- Prometheus scrape intervals: 5s → 30s (83% reduction)
- Delta table size calculation: 10-100x faster
- Parquet-only file traversal (skip _delta_log)

## How to Use

### Running the Spiders

```bash
# Scout Spider (fast discovery)
scrapy crawl scout

# Depth Spider (hidden URLs + value assessment)
scrapy crawl deep_dive

# JS Spider (priority queue + rendering)
scrapy crawl javascript
```

### Using the Priority Queue

```python
from src.common.js_priority_queue import JSPriorityQueue, calculate_js_priority
import redis

# Initialize
redis_client = redis.Redis(host='localhost', port=6379, db=0)
js_queue = JSPriorityQueue(redis_client)

# Enqueue with priority
priority = calculate_js_priority(
    js_confidence=0.85,
    framework_detected="react",
    is_spa=True
)

js_queue.enqueue(
    url="https://portal.example.com/dashboard/",
    priority=priority,  # Will be ~100 (critical)
    metadata={"framework": "react"},
    js_confidence=0.85
)

# Check queue stats
stats = js_queue.get_stats()
print(f"Queue size: {stats['total_size']}")
print(f"Priority distribution: {stats['priority_distribution']}")
```

### Using URL Value Assessment

```python
from src.common.url_value_assessor import URLValueAssessor

assessor = URLValueAssessor()

# Assess a single URL
assessment = assessor.assess_url(
    url="https://www.uconn.edu/research/faculty/",
    depth=2,
    js_confidence=0.3
)

print(f"Value Score: {assessment.value_score}/100")
print(f"Content Likelihood: {assessment.content_likelihood}")
print(f"Recommended Spider: {assessment.recommended_spider}")
print(f"Reasons: {assessment.reasons}")

# Batch assessment
urls_with_context = [
    ("https://example.com/page1", {"depth": 1, "js_confidence": 0.5}),
    ("https://example.com/page2", {"depth": 2, "js_confidence": 0.8}),
]

assessments = assessor.assess_batch(urls_with_context)
```

### Using Hidden URL Extraction

```python
from src.common.hidden_url_extractor import HiddenURLExtractor

# In your spider's parse method:
extractor = HiddenURLExtractor(base_url=response.url)
hidden_urls = extractor.extract_all_hidden_urls(response)

# Results categorized by source:
print(f"Data attributes: {hidden_urls['data_attributes']}")
print(f"JSON-LD: {hidden_urls['json_ld']}")
print(f"JavaScript: {hidden_urls['javascript']}")
print(f"Iframes: {hidden_urls['iframes']}")
print(f"Meta refresh: {hidden_urls['meta_refresh']}")
print(f"Sitemaps: {hidden_urls['sitemaps']}")
print(f"API endpoints: {hidden_urls['api_endpoints']}")
```

## Workflow Examples

### Example 1: Comprehensive Site Crawl

```bash
# Step 1: Fast discovery with Scout
scrapy crawl scout

# Step 2: Deep discovery with Depth Spider
scrapy crawl deep_dive

# Step 3: Render JS-heavy pages
scrapy crawl javascript
```

### Example 2: Priority Queue Workflow

```python
# 1. Scout discovers URLs and routes to JS spider
# (happens automatically in scout_spider.py)

# 2. Depth spider finds hidden URLs
# (happens automatically in deep_dive_spider.py)

# 3. JS spider processes priority queue
# (happens automatically when you run: scrapy crawl javascript)

# 4. Monitor progress
from src.common.js_priority_queue import JSPriorityQueue
import redis

redis_client = redis.Redis()
queue = JSPriorityQueue(redis_client)

stats = queue.get_stats()
print(f"Remaining: {stats['total_size']}")
print(f"Critical: {stats['priority_distribution']['critical']}")
print(f"High: {stats['priority_distribution']['high']}")
```

## Configuration

### Increase JS Spider Concurrency
```python
# In src/stage1/js_spider.py
custom_settings = {
    "CONCURRENT_REQUESTS": 30,  # Increase if you have resources
    "CONCURRENT_REQUESTS_PER_DOMAIN": 15,
    "MEMUSAGE_LIMIT_MB": 16384,  # 16GB
}
```

### Adjust URL Value Thresholds
```python
# In src/common/url_value_assessor.py
class URLValueAssessor:
    HIGH_VALUE_PATTERNS = [
        r"/your-pattern/",  # Add domain-specific patterns
        # ...
    ]
```

### Tune Priority Calculation
```python
# In src/common/js_priority_queue.py
def calculate_js_priority(js_confidence, framework_detected, is_spa):
    base_priority = int(js_confidence * 60)  # Increase base weight
    # ...
```

## Monitoring

### Grafana Dashboards

The system exposes metrics for:

**Priority Queue:**
- `js_priority_queue_size`
- `js_priority_queue_critical_count`
- `js_priority_queue_high_count`

**Depth Spider:**
- `depth_hidden_urls_found`
- `depth_high_value_urls`
- `depth_api_endpoints_discovered`

**JS Spider:**
- `js_render_time_seconds`
- `js_urls_discovered_via_rendering`
- `js_priority_processed_total`

### CLI Monitoring

```bash
# Watch priority queue
redis-cli ZCARD js_spider:priority_queue

# Peek at top priorities
redis-cli ZRANGE js_spider:priority_queue 0 9 WITHSCORES

# Check queue stats
redis-cli HLEN js_spider:priority_queue:metadata
```

## Testing

### Test Priority Queue

```python
# tests/unit/common/test_js_priority_queue.py
import pytest
from src.common.js_priority_queue import JSPriorityQueue
import fakeredis

def test_priority_queue():
    redis_client = fakeredis.FakeStrictRedis()
    queue = JSPriorityQueue(redis_client)

    # Enqueue with different priorities
    queue.enqueue("http://low.com", priority=10)
    queue.enqueue("http://high.com", priority=90)
    queue.enqueue("http://critical.com", priority=100)

    # Dequeue should return highest priority first
    urls = queue.dequeue(count=3)
    assert urls[0]["url"] == "http://critical.com"
    assert urls[1]["url"] == "http://high.com"
    assert urls[2]["url"] == "http://low.com"
```

### Test URL Value Assessment

```python
# tests/unit/common/test_url_value_assessor.py
from src.common.url_value_assessor import URLValueAssessor

def test_high_value_urls():
    assessor = URLValueAssessor()

    # High-value URL
    assessment = assessor.assess_url(
        "https://www.uconn.edu/research/faculty/"
    )
    assert assessment.value_score >= 70
    assert assessment.content_likelihood == "high"

    # Low-value URL
    assessment = assessor.assess_url(
        "https://www.uconn.edu/login"
    )
    assert assessment.value_score < 40
```

## Performance Tips

1. **Memory:**
   - Monitor JS spider memory usage: `docker stats`
   - Reduce concurrency if memory spikes
   - Enable aggressive resource blocking

2. **Throughput:**
   - Increase Scout concurrency for faster discovery
   - Use batch operations in custom code
   - Optimize Redis with pipelining

3. **Quality:**
   - Tune value assessment patterns for your domain
   - Monitor high-value URL rate
   - Adjust priority thresholds based on results

## Troubleshooting

### Priority Queue Not Working
```bash
# Check Redis connectivity
redis-cli ping

# Verify queue exists
redis-cli EXISTS js_spider:priority_queue

# Check queue size
redis-cli ZCARD js_spider:priority_queue
```

### JS Spider Not Processing Queue
- Check spider logs: `scrapy crawl javascript --loglevel=INFO`
- Verify priority queue has items
- Check Playwright installation: `playwright install chromium`

### Hidden URLs Not Discovered
- Verify HiddenURLExtractor is imported correctly
- Check Depth spider logs for extraction stats
- Test extractor with sample response

## Next Steps

1. **Run all three spiders** to see the system in action
2. **Monitor Grafana dashboards** to track metrics
3. **Review discovered URLs** in Delta Lake tables
4. **Tune patterns** in URLValueAssessor for your domain
5. **Adjust priorities** based on crawl results

## Files Changed/Created

### New Files:
- `src/common/js_priority_queue.py` - Priority queue implementation
- `src/common/url_value_assessor.py` - URL value assessment
- `src/common/hidden_url_extractor.py` - Hidden URL extraction
- `docs/MULTI_TIERED_SPIDER_SYSTEM.md` - Comprehensive documentation
- `docs/QUICK_START_MULTI_TIER.md` - This quick start guide

### Modified Files:
- `src/stage1/js_spider.py` - Added priority queue support
- `src/stage1/deep_dive_spider.py` - Enhanced with hidden URL extraction
- `monitoring/metrics_exporter.py` - Optimized metrics calculation
- `monitoring/prometheus.yml` - Reduced scrape intervals

## Questions?

See the full documentation: `docs/MULTI_TIERED_SPIDER_SYSTEM.md`
