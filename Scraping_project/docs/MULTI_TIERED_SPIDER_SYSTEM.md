# Multi-Tiered URL Discovery System

## Overview

This document describes the sophisticated multi-tiered web crawling architecture designed for maximum URL discovery and intelligent routing.

## Architecture

The system consists of three specialized spiders working in concert:

```
┌─────────────┐
│ Scout Spider│ (Fast Discovery)
└──────┬──────┘
       │
       ├──────► High-throughput HTML crawling
       │        Dual-queue routing (JS + Stage2)
       │        Basic URL extraction
       │
       ▼
┌──────────────────────────────────────┐
│         URL Routing System           │
│  (Intelligent Value Assessment)      │
└───┬─────────────────┬────────────────┘
    │                 │
    ▼                 ▼
┌───────────┐   ┌────────────────┐
│ JS Spider │   │  Depth Spider  │
│(Aggressive│   │   (Discovery)  │
│   Async)  │   │                │
└───────────┘   └────────────────┘
```

## Components

### 1. Scout Spider
**Purpose:** Fast, high-throughput URL discovery

**Features:**
- Lightweight HTML processing
- Batch Redis operations for deduplication
- Dual-queue routing (JS spider + Stage 2)
- Static asset filtering
- Offsite link tracking

**Performance:**
- High concurrency (50+ concurrent requests)
- Minimal processing per page
- Optimized for speed over depth

**Location:** `src/stage1/scout_spider.py`

---

### 2. JavaScript Spider (Enhanced)
**Purpose:** Aggressive JavaScript rendering with priority queue

**Features:**
- **Priority Queue System:** Redis sorted sets for intelligent URL scheduling
- **Priority Levels:**
  - 100: Critical (SPA, React/Vue/Angular detected)
  - 50-75: High (high JS confidence, framework hints)
  - 25-49: Medium (moderate JS signals)
  - 0-24: Low (minimal JS)
- **Aggressive Async Processing:**
  - 20 concurrent requests (up from 10)
  - 10 concurrent requests per domain (up from 5)
  - Auto-throttle for optimal throughput
- **Resource Blocking:** Images, CSS, fonts blocked for 3-5x faster rendering
- **Smart Scrolling:** Triggers lazy-loaded content
- **API Endpoint Capture:** Intercepts JSON responses during rendering

**Performance:**
- Playwright/Chromium headless browser
- 12GB memory limit
- Batch dequeuing for efficiency

**Location:** `src/stage1/js_spider.py`

**Priority Queue:** `src/common/js_priority_queue.py`

---

### 3. Depth Spider (Enhanced)
**Purpose:** Discover hidden and embedded URLs

**Features:**
- **Hidden URL Extraction:**
  - Data attributes (data-url, data-src, data-href)
  - JSON-LD structured data
  - Embedded JavaScript configurations
  - iframe sources
  - Meta refresh redirects
  - Sitemap references
  - API endpoints in JS code
- **Intelligent URL Value Assessment:**
  - Pattern-based scoring (0-100)
  - Content likelihood estimation
  - Smart spider routing recommendations
- **Deep Link Discovery:**
  - Archive/collection patterns
  - Database interfaces
  - Search interfaces
  - Category/tag systems

**URL Value Assessment Criteria:**
- High-value patterns: `/research/`, `/faculty/`, `/publications/`, etc.
- Low-value patterns: `/login`, `/admin/`, `/api/`, etc.
- Document boosts: PDFs, Word docs, presentations
- JS requirement detection
- URL structure analysis (path depth, query params)
- Domain assessment (.edu/.gov boost)

**Performance:**
- Moderate concurrency (20-30 concurrent requests)
- Balanced between speed and thoroughness
- Comprehensive URL extraction

**Location:** `src/stage1/deep_dive_spider.py`

**Hidden URL Extractor:** `src/common/hidden_url_extractor.py`

**URL Value Assessor:** `src/common/url_value_assessor.py`

---

## URL Routing Logic

### How URLs Are Routed

1. **Scout Spider discovers URL**
2. **URL Value Assessor evaluates:**
   - Value score (0-100)
   - Content likelihood (high/medium/low)
   - Recommended spider (js/depth/scout)
3. **URL routed based on characteristics:**

```python
# High JS confidence (> 0.6) → JS Spider
# JS patterns (/app/, /dashboard/, /#/) → JS Spider
# Depth patterns (/archive/, /collection/) → Depth Spider
# Low value (< 30) → Skip or low-priority scout
# Default → Scout Spider
```

### Priority Calculation for JS Spider

```python
def calculate_js_priority(
    js_confidence: float,  # 0.0-1.0
    framework_detected: str,  # "react", "vue", etc.
    is_spa: bool,
) -> int:
    """
    Priority scoring:
    - Base: js_confidence * 50 (0-50)
    - Framework boost: +30-50
    - SPA boost: +50
    - URL heuristics: +10
    - Capped at 100
    """
```

---

## Data Flow

### 1. Initial Discovery (Scout)
```
Seed URLs → Scout Spider → Extract Links
                          ↓
                    Redis Deduplication
                          ↓
                    ┌─────┴─────┐
                    ▼           ▼
              JS Queue    Stage 2 Queue
```

### 2. JavaScript Processing
```
JS Priority Queue (Redis Sorted Set)
         ↓
Priority-ordered dequeuing
         ↓
Playwright rendering
         ↓
Discovered URLs → Back to Scout/Depth
```

### 3. Depth Discovery
```
HTML Page → HiddenURLExtractor
            ↓
    ┌───────┴────────┬──────────┬────────────┐
    ▼                ▼          ▼            ▼
Data Attrs      JSON-LD    JavaScript    Iframes
    │                │          │            │
    └────────────────┴──────────┴────────────┘
                     ▼
            URL Value Assessment
                     ▼
         ┌───────────┴───────────┐
         ▼                       ▼
    Route to JS           Route to Depth
```

---

## Redis Data Structures

### Priority Queue (Sorted Set)
```
Key: js_spider:priority_queue
Type: Sorted Set
Score: -priority (negative for descending order)
Members: URLs

Example:
ZADD js_spider:priority_queue -100 "https://portal.example.com/app/"
ZADD js_spider:priority_queue -50 "https://example.com/dashboard/"
ZADD js_spider:priority_queue -10 "https://example.com/page.html"
```

### Deduplication (Set)
```
Key: js_spider:priority_queue:hashes
Type: Set
Members: URL hashes (SHA256)
```

### Metadata (Hash)
```
Key: js_spider:priority_queue:metadata
Type: Hash
Fields: URLs
Values: JSON metadata
```

---

## Configuration

### Scout Spider Settings
```yaml
scout:
  concurrent_requests: 50
  concurrent_requests_per_domain: 20
  download_delay: 0.1
  autothrottle_enabled: true
```

### JS Spider Settings
```yaml
javascript:
  concurrent_requests: 20
  concurrent_requests_per_domain: 10
  download_timeout: 60
  memusage_limit_mb: 12288
  playwright_browser_type: chromium
  playwright_headless: true
```

### Depth Spider Settings
```yaml
deep_dive:
  concurrent_requests: 25
  concurrent_requests_per_domain: 10
  download_delay: 0.2
  max_depth: 10
```

---

## Monitoring

### Key Metrics

**Scout Spider:**
- `urls_per_minute`: Discovery rate
- `pages_queued_js`: URLs sent to JS spider
- `pages_queued_stage2`: URLs sent to Stage 2
- `static_discarded`: Assets filtered out

**JS Spider:**
- `priority_queue_size`: Pending JS renders
- `priority_distribution`: Critical/High/Medium/Low counts
- `render_time_seconds`: Average render time
- `urls_discovered_via_js`: Dynamic content found

**Depth Spider:**
- `hidden_urls_found`: Total hidden URLs discovered
- `data_attributes`: URLs from data-* attributes
- `json_ld`: URLs from JSON-LD
- `javascript`: URLs from JS code
- `api_endpoints`: API endpoints discovered
- `high_value_urls`: Valuable content found

### Dashboard Optimizations

**Prometheus Scrape Intervals:**
- Reduced from 5s/10s to 30s (83% less load)
- Still provides real-time monitoring
- Reduces Prometheus memory usage

**Metrics Exporter Optimizations:**
- Delta table size calculation: 10-100x faster (parquet files only)
- Metadata-based counting (avoids full table reads)
- 30s update interval (was 5s)

---

## Usage Examples

### 1. Enqueue URL to JS Spider (High Priority)
```python
from src.common.js_priority_queue import JSPriorityQueue
import redis

redis_client = redis.Redis(host='localhost', port=6379, db=0)
js_queue = JSPriorityQueue(redis_client)

# Critical SPA page
js_queue.enqueue(
    url="https://portal.uconn.edu/app/dashboard/",
    priority=100,
    metadata={"framework": "react", "is_spa": True},
    js_confidence=0.95
)
```

### 2. Assess URL Value
```python
from src.common.url_value_assessor import URLValueAssessor

assessor = URLValueAssessor()

assessment = assessor.assess_url(
    url="https://www.uconn.edu/research/publications/2024/paper.pdf",
    depth=2,
    js_confidence=0.0
)

print(f"Value Score: {assessment.value_score}/100")
print(f"Recommended Spider: {assessment.recommended_spider}")
print(f"Reasons: {assessment.reasons}")
```

### 3. Extract Hidden URLs
```python
from src.common.hidden_url_extractor import HiddenURLExtractor

extractor = HiddenURLExtractor(base_url=response.url)
hidden_urls = extractor.extract_all_hidden_urls(response)

print(f"Data attributes: {len(hidden_urls['data_attributes'])}")
print(f"JSON-LD: {len(hidden_urls['json_ld'])}")
print(f"JavaScript: {len(hidden_urls['javascript'])}")
print(f"API endpoints: {len(hidden_urls['api_endpoints'])}")
```

---

## Performance Characteristics

### Throughput Comparison

| Spider | Concurrent Requests | Pages/Minute | Memory Usage |
|--------|---------------------|--------------|--------------|
| Scout  | 50                  | 500-1000     | 512MB        |
| JS     | 20                  | 20-50        | 8-12GB       |
| Depth  | 25                  | 100-200      | 1-2GB        |

### URL Discovery Rates

- **Scout:** 1000+ URLs/minute (shallow)
- **Depth:** 500+ URLs/minute (deep, hidden)
- **JS:** 100+ URLs/minute (dynamic content)

---

## Best Practices

### 1. Spider Selection
- Use **Scout** for broad, fast discovery
- Use **JS Spider** for known JavaScript-heavy sites
- Use **Depth Spider** when you need comprehensive URL coverage

### 2. Priority Queue Management
- Assign priority 100 for critical SPAs
- Use priority 50-75 for dashboard/portal pages
- Reserve priority < 25 for background processing

### 3. Value Assessment
- Adjust patterns in `URLValueAssessor` for your domain
- Monitor `high_value_urls` metric to tune thresholds
- Filter low-value URLs early to save resources

### 4. Monitoring
- Watch `priority_queue_size` - should drain steadily
- Monitor `render_time_seconds` - optimize if > 10s average
- Track `hidden_urls_found` to measure depth effectiveness

---

## Troubleshooting

### High Memory Usage (JS Spider)
- Reduce `CONCURRENT_REQUESTS` from 20 to 10
- Enable more aggressive resource blocking
- Check for memory leaks in Playwright

### Low Discovery Rate
- Increase Scout spider concurrency
- Check Redis deduplication - may be filtering too much
- Verify URL patterns in value assessor

### Priority Queue Not Draining
- Check JS spider is running
- Verify Redis connectivity
- Review priority distribution - may be too many low-priority URLs

---

## Future Enhancements

1. **Machine Learning URL Valuation:**
   - Train model on crawl data
   - Predict content value before fetching
   - Dynamic priority adjustment

2. **Distributed Priority Queue:**
   - Multiple JS spider instances
   - Load balancing across workers
   - Priority inheritance

3. **Adaptive Crawling:**
   - Auto-adjust spider selection based on site characteristics
   - Dynamic concurrency tuning
   - Smart retry strategies

4. **Advanced JS Detection:**
   - Framework detection from HTML
   - SPA pattern recognition
   - API endpoint prediction

---

## Related Files

- **Spider Implementations:**
  - `src/stage1/scout_spider.py`
  - `src/stage1/js_spider.py`
  - `src/stage1/deep_dive_spider.py`
  - `src/stage1/base_spider.py`

- **URL Discovery:**
  - `src/common/hidden_url_extractor.py`
  - `src/common/url_extractor.py`
  - `src/common/url_value_assessor.py`

- **Queue Management:**
  - `src/common/js_priority_queue.py`
  - `src/common/redis_manager.py`

- **Monitoring:**
  - `monitoring/metrics_exporter.py`
  - `monitoring/prometheus.yml`
  - `monitoring/dashboards/`

---

## Questions?

For more information, see:
- [Configuration Guide](./CONFIGURATION.md)
- [Monitoring Guide](./MONITORING.md)
- [Development Guide](./DEVELOPMENT.md)
