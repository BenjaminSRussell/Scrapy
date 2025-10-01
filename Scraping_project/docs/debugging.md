# Debugging Guide for UConn Scraper

This guide provides detailed strategies for debugging the web scraping pipeline, with special focus on Scrapy spiders.

## General Debugging Strategies

### 1. Inspect Configuration

Before running a complex pipeline, you can check the fully resolved configuration to ensure that your YAML files and environment variables are being loaded correctly. Use the `--config-only` flag.

```bash
# See the merged configuration for the development environment
python main.py --env development --config-only

# Pipe it to a file for easier inspection
python main.py --env development --config-only > config_snapshot.txt
```

This is useful for verifying:
- Environment-specific settings (`development` vs. `production`).
- Environment variable overrides (e.g., `SCRAPY_DOWNLOAD_DELAY`).
- Correct file paths and pipeline parameters.

### 2. Enable Debug Logging

```bash
# Run with debug logging
python main.py --stage 1 --log-level DEBUG

# Or set environment variable
export LOG_LEVEL=DEBUG
python main.py --stage all
```

### 3. Check Log Files

```bash
# View logs in real-time
tail -f data/logs/pipeline.log

# Search for errors
grep ERROR data/logs/pipeline.log

# View error log specifically
cat data/logs/error.log
```

### 4. Use Structured Logging

Enable JSON logging for easier parsing:

```yaml
# config/development.yml
logging:
  structured: true
```

Then parse logs:

```bash
# Pretty print JSON logs
cat data/logs/pipeline.log | jq '.message, .level'

# Filter errors
cat data/logs/pipeline.log | jq 'select(.level=="ERROR")'
```

## Scrapy Spider Debugging

### Interactive Shell Method

#### Open Scrapy Shell

```bash
# Test a specific URL
scrapy shell "https://uconn.edu"

# With custom settings
scrapy shell -s USER_AGENT="Custom-Agent" "https://uconn.edu"
```

#### Shell Commands

Once in the shell:

```python
# View response
>>> response
<200 https://uconn.edu>

# Check status code
>>> response.status
200

# View headers
>>> response.headers
{b'Content-Type': [b'text/html'], ...}

# Test CSS selectors
>>> response.css('title::text').get()
'UConn | University of Connecticut'

# Test XPath selectors
>>> response.xpath('//title/text()').get()
'UConn | University of Connecticut'

# Get all links
>>> response.css('a::attr(href)').getall()
['/', '/about', ...]

# Open response in browser
>>> view(response)

# Re-fetch with different settings
>>> fetch("https://uconn.edu", headers={'User-Agent': 'Mozilla/5.0'})

# Access spider (if available)
>>> spider
<DiscoverySpider 'discovery' at 0x...>
```

### In-Spider Debugging

#### Method 1: Interactive Inspection

Add to your spider code:

```python
from scrapy.shell import inspect_response

class DiscoverySpider(scrapy.Spider):
    def parse(self, response):
        # Drop into interactive shell here
        inspect_response(response, self)

        # Continue with normal parsing
        for link in response.css('a::attr(href)'):
            yield {'url': link.get()}
```

#### Method 2: Conditional Breakpoints

```python
def parse(self, response):
    # Only debug specific URLs
    if 'troublesome-page' in response.url:
        inspect_response(response, self)

    # Normal processing
    yield from self.parse_links(response)
```

#### Method 3: Enhanced Logging

```python
import logging

class DiscoverySpider(scrapy.Spider):
    def parse(self, response):
        # Log response details
        self.logger.info(f"Parsing: {response.url}")
        self.logger.debug(f"Status: {response.status}")
        self.logger.debug(f"Size: {len(response.body)} bytes")

        # Log selector results
        links = response.css('a::attr(href)').getall()
        self.logger.info(f"Found {len(links)} links")
        self.logger.debug(f"Links: {links[:10]}")  # First 10

        # Log items
        for item in self.extract_items(response):
            self.logger.debug(f"Item: {item}")
            yield item
```

### Testing Specific URLs

#### Create Test Input

```bash
# Create test CSV with problematic URLs
cat > test_urls.csv << EOF
https://uconn.edu/problematic-page
https://catalog.uconn.edu/another-page
EOF
```

#### Run Spider with Test File

```bash
# Discovery spider
scrapy crawl discovery \
    -a seed_file=test_urls.csv \
    -a max_depth=1 \
    -L DEBUG

# Or via orchestrator
python -m src.orchestrator.main --stage 1 --log-level DEBUG
```

### Debugging Link Extraction

#### Check What Links Are Found

```python
def parse(self, response):
    from scrapy.linkextractors import LinkExtractor

    le = LinkExtractor(allow_domains=self.allowed_domains)
    links = le.extract_links(response)

    # Log details
    self.logger.info(f"LinkExtractor found {len(links)} links")
    for link in links[:5]:  # First 5
        self.logger.debug(f"  URL: {link.url}")
        self.logger.debug(f"  Text: {link.text}")
        self.logger.debug(f"  Fragment: {link.fragment}")
```

#### Debug Dynamic URL Discovery

```python
def _discover_dynamic_sources(self, response, current_depth):
    # Log what we're checking
    self.logger.debug(f"Checking {response.url} for dynamic sources")

    # Log data attributes found
    for attr in self.data_attribute_candidates:
        values = response.xpath(f'//*[@{attr}]/@{attr}').getall()
        if values:
            self.logger.debug(f"Found {attr}: {values}")

    # Log JavaScript patterns
    scripts = response.xpath('//script[not(@src)]/text()').getall()
    self.logger.debug(f"Found {len(scripts)} inline scripts")

    # Continue with normal processing
    yield from self._process_candidates(response, current_depth)
```

### Debugging Item Pipelines

#### Log Pipeline Processing

```python
class Stage1Pipeline:
    def process_item(self, item, spider):
        spider.logger.debug(f"Pipeline received: {item}")

        # Log transformations
        original_url = item.get('url')
        canonical_url = canonicalize_url(original_url)

        if original_url != canonical_url:
            spider.logger.debug(
                f"Canonicalized: {original_url} -> {canonical_url}"
            )

        return item
```

#### Track Pipeline Errors

```python
class Stage1Pipeline:
    def process_item(self, item, spider):
        try:
            # Process item
            result = self.save_item(item)
            spider.logger.debug(f"Saved: {item['url']}")
            return item
        except Exception as e:
            spider.logger.error(
                f"Failed to process item: {item}",
                exc_info=True
            )
            # Re-raise or handle
            raise DropItem(f"Processing failed: {e}")
```

### Debugging HTTP Requests

#### Enable Request/Response Logging

```python
# In settings.py or spider settings
DOWNLOADER_MIDDLEWARES = {
    'scrapy.downloadermiddlewares.httpcache.HttpCacheMiddleware': 900,
}

# Enable HTTP cache for debugging
HTTPCACHE_ENABLED = True
HTTPCACHE_DIR = 'data/cache/scrapy_httpcache'
HTTPCACHE_EXPIRATION_SECS = 3600  # 1 hour
```

#### Log Request Details

```python
class DiscoverySpider(scrapy.Spider):
    def make_requests_from_url(self, url):
        self.logger.debug(f"Making request to: {url}")
        return scrapy.Request(
            url,
            callback=self.parse,
            errback=self.handle_error,
            dont_filter=False
        )

    def handle_error(self, failure):
        self.logger.error(f"Request failed: {failure.request.url}")
        self.logger.error(f"Error: {failure.value}")
        self.logger.error(f"Type: {failure.type}")
```

### Debugging Spider Statistics

#### Access Stats in Spider

```python
def closed(self, reason):
    stats = self.crawler.stats.get_stats()

    # Log all stats
    self.logger.info("=" * 60)
    self.logger.info("SPIDER STATISTICS")
    self.logger.info("=" * 60)

    # Items
    self.logger.info(f"Items scraped: {stats.get('item_scraped_count', 0)}")
    self.logger.info(f"Items dropped: {stats.get('item_dropped_count', 0)}")

    # Requests
    self.logger.info(f"Requests: {stats.get('downloader/request_count', 0)}")
    self.logger.info(f"Responses 200: {stats.get('downloader/response_status_count/200', 0)}")
    self.logger.info(f"Responses 404: {stats.get('downloader/response_status_count/404', 0)}")

    # Errors
    self.logger.info(f"Exceptions: {stats.get('downloader/exception_count', 0)}")
    self.logger.info(f"Spider exceptions: {stats.get('spider_exceptions', 0)}")

    # Performance
    self.logger.info(f"Finish reason: {stats.get('finish_reason')}")
    self.logger.info(f"Elapsed time: {stats.get('elapsed_time_seconds', 0):.2f}s")
```

## Stage 2 (Validation) Debugging

### Enable Verbose Logging

```python
import logging

# In validator
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

async def validate_url(self, session, url, url_hash):
    logger.debug(f"Validating: {url}")

    try:
        # Try HEAD request
        async with session.head(url, timeout=self.timeout) as response:
            logger.debug(f"HEAD {url}: {response.status}")
            return self._create_result(response, url, url_hash, "HEAD")
    except Exception as e:
        logger.debug(f"HEAD failed for {url}: {e}, trying GET")
        # Fallback to GET
        return await self._validate_with_get(session, url, url_hash)
```

### Track Batch Processing

```python
async def validate_batch(self, batch, batch_id):
    logger.info(f"Processing batch {batch_id} with {len(batch)} URLs")

    start_time = time.time()
    successful = 0
    failed = 0

    for item in batch:
        try:
            result = await self.validate_url(item.url, item.url_hash)
            if result.is_valid:
                successful += 1
            else:
                failed += 1
        except Exception as e:
            logger.error(f"Error validating {item.url}: {e}", exc_info=True)
            failed += 1

    elapsed = time.time() - start_time
    logger.info(
        f"Batch {batch_id} complete: {successful} OK, {failed} failed, "
        f"{elapsed:.2f}s ({len(batch)/elapsed:.1f} URLs/sec)"
    )
```

### Debug Checkpoint Issues

```python
# Check checkpoint status
checkpoint = checkpoint_manager.get_checkpoint("stage2_validation")
stats = checkpoint.get_stats()
logger.info(f"Checkpoint stats: {stats}")

# Validate checkpoint
is_valid, reason = checkpoint.validate_checkpoint()
if not is_valid:
    logger.warning(f"Checkpoint invalid: {reason}")
    checkpoint.reset()
```

## Stage 3 (Enrichment) Debugging

### Debug NLP Processing

```python
from src.common.nlp import get_registry

# Check NLP registry
registry = get_registry()
logger.info(f"NLP Registry: {type(registry)}")
logger.info(f"spaCy model: {registry.spacy_nlp}")
logger.info(f"Device: {registry.device}")

# Test NLP extraction
text = "Sample text from UConn website"
entities, keywords = registry.extract_with_spacy(text, top_k=10)
logger.debug(f"Entities: {entities}")
logger.debug(f"Keywords: {keywords}")
```

### Debug Content Extraction

```python
def parse(self, response):
    # Log raw content
    logger.debug(f"Content length: {len(response.body)}")
    logger.debug(f"Content type: {response.headers.get('Content-Type')}")

    # Log extracted text
    text = self.extract_text(response)
    logger.debug(f"Extracted text length: {len(text)}")
    logger.debug(f"First 200 chars: {text[:200]}")

    # Log NLP results
    entities, keywords = self.extract_nlp(text)
    logger.debug(f"Entities ({len(entities)}): {entities}")
    logger.debug(f"Keywords ({len(keywords)}): {keywords}")
```

## Common Issues and Solutions

### Issue: Spider Not Finding Any Links

**Diagnosis:**
```bash
scrapy shell "https://problematic-url.com"
>>> response.css('a::attr(href)').getall()
[]
```

**Solutions:**
1. Check if JavaScript is required (view(response) vs browser)
2. Verify selectors are correct
3. Check robots.txt compliance
4. Verify domain filtering

### Issue: Memory Leaks

**Diagnosis:**
```python
import tracemalloc
tracemalloc.start()
# Run spider
current, peak = tracemalloc.get_traced_memory()
print(f"Peak memory: {peak / 1024 / 1024:.2f} MB")
```

**Solutions:**
1. Limit concurrent requests
2. Use generators instead of lists
3. Clear caches periodically
4. Check for circular references

### Issue: Slow Performance

**Diagnosis:**
```bash
# Profile spider
python -m cProfile -o profile.stats -m scrapy crawl discovery
python -m pstats profile.stats
```

**Solutions:**
1. Increase CONCURRENT_REQUESTS
2. Reduce DOWNLOAD_DELAY
3. Enable DNS caching
4. Use connection pooling

### Issue: Items Not Being Saved

**Diagnosis:**
```python
# Add logging to pipeline
class MyPipeline:
    def open_spider(self, spider):
        spider.logger.info("Pipeline opened")

    def close_spider(self, spider):
        spider.logger.info(f"Pipeline closed, processed {self.count} items")

    def process_item(self, item, spider):
        spider.logger.debug(f"Processing: {item}")
        self.count += 1
        return item
```

**Solutions:**
1. Check ITEM_PIPELINES settings
2. Verify pipeline priority numbers
3. Ensure pipeline returns items
4. Check for exceptions in pipeline

## Advanced Debugging Tools

### 1. Scrapy Stats Collector

```python
from scrapy.statscollectors import MemoryStatsCollector

# Custom stats
stats = MemoryStatsCollector(crawler)
stats.set_value('custom_metric', 100)
stats.inc_value('custom_counter')
```

### 2. Request/Response Middleware Logging

```python
class LoggingMiddleware:
    def process_request(self, request, spider):
        spider.logger.debug(f"Request: {request.url}")
        return None

    def process_response(self, request, response, spider):
        spider.logger.debug(
            f"Response: {response.url} [{response.status}]"
        )
        return response
```

### 3. Custom Telemetry

```python
import time

class TelemetrySpider(scrapy.Spider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request_times = {}

    def make_request(self, url):
        self.request_times[url] = time.time()
        return scrapy.Request(url, callback=self.parse)

    def parse(self, response):
        elapsed = time.time() - self.request_times.get(response.url, 0)
        self.logger.info(f"{response.url} took {elapsed:.2f}s")
```

## Getting Help

If you're still stuck:

1. Check logs thoroughly (`data/logs/`)
2. Review [architecture.md](architecture.md) for system understanding
3. Search existing issues on GitHub
4. Create detailed bug report with:
   - Steps to reproduce
   - Expected vs actual behavior
   - Full error messages and logs
   - Environment details
