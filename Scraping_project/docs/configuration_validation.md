# Configuration Validation System

The scraping pipeline uses a **multi-layered validation system** with Pydantic-based schema validation and comprehensive health checks to ensure configuration correctness before the pipeline starts. This prevents runtime errors caused by typos, type mismatches, invalid values, or missing dependencies.

## Validation Layers

### Layer 1: Schema Validation (Pydantic)
Validates configuration structure, types, ranges, and relationships using strict Pydantic schemas.

### Layer 2: Health Checks (Runtime)
Validates runtime requirements including file system access, dependency availability, and resource settings.

### Layer 3: Startup Integration
Automatically runs all validations when the pipeline starts, failing fast with clear error messages.

## Key Features

### 1. **Fail-Fast Validation**
The configuration is validated immediately when the pipeline starts. If there are any errors, the application will exit with a detailed error message before any scraping begins.

### 2. **Type Checking with Coercion**
Pydantic automatically coerces compatible types when possible:
- ✅ String `"5"` → Integer `5` (valid)
- ✅ String `"0.5"` → Float `0.5` (valid)
- ❌ String `"not_a_number"` → Integer (error)

### 3. **Unknown Key Detection (Typo Prevention)**
The schema uses `extra='forbid'` to reject any unknown configuration keys, catching typos:

```yaml
# ❌ INVALID - will be caught
stages:
  discovery:
    maxDepth: 5  # Typo: should be 'max_depth'
```

**Error message:**
```
Configuration validation failed:

  ❌ Unknown key 'maxDepth' at stages -> discovery -> maxDepth
     This might be a typo. Check your configuration file.
```

### 4. **Range Validation**
All numeric values have validated ranges:

```yaml
# ❌ INVALID - out of range
stages:
  discovery:
    max_depth: 15  # Must be between 0 and 10
```

**Error message:**
```
Configuration validation failed:

  ❌ Value error at stages -> discovery -> max_depth: Input should be less than or equal to 10
```

### 5. **Enum Validation**
String values with limited options are validated:

```yaml
# ❌ INVALID - not a valid log level
logging:
  level: "TRACE"  # Must be DEBUG, INFO, WARNING, ERROR, or CRITICAL
```

### 6. **Cross-Field Validation**
Complex validation rules across multiple fields:

```yaml
# ❌ INVALID - per-domain limit exceeds total
scrapy:
  concurrent_requests: 10
  concurrent_requests_per_domain: 20  # Must be <= concurrent_requests
```

**Error message:**
```
Configuration validation failed:

  ❌ Value error at scrapy: concurrent_requests_per_domain (20) cannot exceed concurrent_requests (10)
```

### 7. **Domain Format Validation**
Ensures domain names follow proper format:

```yaml
# ❌ INVALID - invalid domain format
stages:
  discovery:
    allowed_domains:
      - "invalid_domain"  # Missing TLD
      - "example.com"     # Valid
```

### 8. **MIME Type Validation**
Content types must follow standard MIME format:

```yaml
# ❌ INVALID - not a valid MIME type
stages:
  enrichment:
    content_types:
      enabled_types:
        - "not-a-mime-type"     # Invalid
        - "text/html"           # Valid
        - "application/pdf"     # Valid
```

### 9. **Engine/Browser Compatibility**
Validates that browser configuration is compatible:

```yaml
# ❌ INVALID - Selenium doesn't support WebKit
stages:
  enrichment:
    headless_browser:
      engine: "selenium"
      browser_type: "webkit"  # Only Playwright supports WebKit
```

## Validation Rules Reference

### Scrapy Configuration
| Field | Type | Range/Values | Default |
|-------|------|--------------|---------|
| `concurrent_requests` | int | 1-1000 | 32 |
| `concurrent_requests_per_domain` | int | 1-100, ≤ concurrent_requests | 16 |
| `concurrent_requests_per_ip` | int | 1-100, ≤ concurrent_requests | 16 |
| `download_delay` | float | 0.0-60.0 | 0.1 |
| `download_timeout` | int | 1-300 | 10 |
| `dns_timeout` | int | 1-60 | 5 |
| `retry_times` | int | 0-10 | 2 |
| `log_level` | str | DEBUG, INFO, WARNING, ERROR, CRITICAL | INFO |

### Discovery Stage
| Field | Type | Range/Values | Default |
|-------|------|--------------|---------|
| `max_depth` | int | 0-10 | 3 |
| `allowed_domains` | list[str] | Valid domain format | ["uconn.edu"] |
| `use_persistent_dedup` | bool | - | true |

### Headless Browser
| Field | Type | Range/Values | Default |
|-------|------|--------------|---------|
| `enabled` | bool | - | false |
| `engine` | str | playwright, selenium | playwright |
| `browser_type` | str | chromium, firefox, webkit, chrome | chromium |
| `timeout` | int | 1000-300000 (ms) | 30000 |
| `viewport.width` | int | 320-7680 | 1920 |
| `viewport.height` | int | 240-4320 | 1080 |

**Note:** Selenium does not support `webkit` browser type.

### Validation Stage
| Field | Type | Range/Values | Default |
|-------|------|--------------|---------|
| `max_workers` | int | 1-100 | 16 |
| `timeout` | int | 1-300 | 15 |

### Enrichment Stage
| Field | Type | Range/Values | Default |
|-------|------|--------------|---------|
| `nlp_enabled` | bool | - | true |
| `max_text_length` | int | 100-1000000 | 20000 |
| `top_keywords` | int | 1-100 | 15 |
| `batch_size` | int | 1-10000 | 1000 |

### PDF Configuration
| Field | Type | Range/Values | Default |
|-------|------|--------------|---------|
| `extract_text` | bool | - | true |
| `extract_metadata` | bool | - | true |
| `max_pages` | int | 1-10000 | 100 |

### Queue Configuration
| Field | Type | Range/Values | Default |
|-------|------|--------------|---------|
| `max_queue_size` | int | 100-1000000 | 10000 |
| `batch_size` | int | 1-10000, ≤ max_queue_size | 1000 |
| `backpressure_warning_threshold` | float | 0.0-1.0, < critical | 0.8 |
| `backpressure_critical_threshold` | float | 0.0-1.0, > warning | 0.95 |

### Logging Configuration
| Field | Type | Range/Values | Default |
|-------|------|--------------|---------|
| `level` | str | DEBUG, INFO, WARNING, ERROR, CRITICAL | INFO |
| `max_bytes` | int | 1024-1073741824 (1GB) | 10485760 (10MB) |
| `backup_count` | int | 0-100 | 3 |

## Common Validation Errors

### Type Error: String Instead of Number

**Config:**
```yaml
stages:
  discovery:
    max_depth: "not_a_number"
```

**Error:**
```
Configuration validation failed:

  ❌ Type error at stages -> discovery -> max_depth: Input should be a valid integer
     Got: not_a_number (type: str)
```

**Fix:** Use a valid integer: `max_depth: 5`

### Unknown Key (Typo)

**Config:**
```yaml
scrapy:
  concurent_requests: 32  # Typo in 'concurrent'
```

**Error:**
```
Configuration validation failed:

  ❌ Unknown key 'concurent_requests' at scrapy -> concurent_requests
     This might be a typo. Check your configuration file.
```

**Fix:** Correct the spelling: `concurrent_requests: 32`

### Value Out of Range

**Config:**
```yaml
stages:
  discovery:
    max_depth: 15
```

**Error:**
```
Configuration validation failed:

  ❌ Value error at stages -> discovery -> max_depth: Input should be less than or equal to 10
```

**Fix:** Use a value within range: `max_depth: 5`

### Invalid Domain Format

**Config:**
```yaml
stages:
  discovery:
    allowed_domains:
      - "my_domain"
```

**Error:**
```
Configuration validation failed:

  ❌ Value error at stages -> discovery -> allowed_domains: Invalid domain format: my_domain
```

**Fix:** Use proper domain format: `allowed_domains: ["example.com"]`

### Incompatible Browser Configuration

**Config:**
```yaml
stages:
  enrichment:
    headless_browser:
      engine: "selenium"
      browser_type: "webkit"
```

**Error:**
```
Configuration validation failed:

  ❌ Value error at stages -> enrichment -> headless_browser: Selenium does not support WebKit browser. Use 'chromium', 'firefox', or 'chrome', or switch to 'playwright' engine.
```

**Fix:** Use compatible combination:
```yaml
headless_browser:
  engine: "playwright"
  browser_type: "webkit"
```
or
```yaml
headless_browser:
  engine: "selenium"
  browser_type: "chromium"
```

## Health Check System (New)

The health check system performs runtime validations beyond schema validation:

### File System Checks
- ✅ Seed file exists and is accessible
- ✅ Output directories can be created
- ✅ Data directories have write permissions
- ✅ Dedup cache directory is accessible

### Dependency Checks
- ✅ spaCy models are installed (if NLP enabled)
- ✅ Playwright/Selenium installed (if headless browser enabled)
- ✅ Playwright browsers installed
- ✅ Transformer models available (if transformers enabled)

### Resource Limit Checks
- ⚠️ Warns on very high concurrency settings
- ⚠️ Warns on excessive queue sizes
- ⚠️ Warns on high browser concurrent limits
- ⚠️ Warns on zero download delay (no rate limiting)

### Performance Checks
- ℹ️ Identifies potential performance bottlenecks
- ℹ️ Suggests optimizations for resource-intensive settings

### Example Health Check Output

```bash
================================================================================
Configuration Health Check Report
================================================================================

❌ ERRORS (2):
--------------------------------------------------------------------------------

  [FILESYSTEM] Seed file not found: data/raw/uconn_urls.csv
  💡 Create the seed file or update the path in configuration

  [DEPENDENCY] spaCy model 'en_core_web_sm' not installed
  💡 Run: python -m spacy download en_core_web_sm

⚠️  WARNINGS (1):
--------------------------------------------------------------------------------

  [LOGIC] Very high concurrent_requests: 200
  💡 Consider lowering to avoid overwhelming target servers and local resources

================================================================================
❌ Status: FAILED - Please fix errors before running pipeline
================================================================================
```

## Command-Line Usage

### Validate Configuration Only
Run comprehensive validation without executing the pipeline:

```bash
# Validate configuration and run health checks
python -m src.orchestrator.main --env development --validate-only
```

This will:
1. Load configuration file
2. Run Pydantic schema validation
3. Run comprehensive health checks
4. Print detailed report
5. Exit with code 0 (success) or 1 (failure)

### View Configuration
Display the loaded and validated configuration:

```bash
python -m src.orchestrator.main --env development --config-only
```

### Normal Pipeline Execution
Validation runs automatically at startup:

```bash
# Runs validation automatically, then executes pipeline
python -m src.orchestrator.main --env development
```

## Testing Configuration

### Test Configuration Programmatically

```python
from src.orchestrator.config import Config, ConfigValidationError
from src.common.config_validator import validate_config_health

try:
    # Schema validation
    config = Config(env='development', validate=True)

    # Health check
    is_healthy = validate_config_health(config)

    if is_healthy:
        print("✅ Configuration is valid and healthy!")
    else:
        print("⚠️ Configuration has warnings or errors")

except ConfigValidationError as e:
    print(f"❌ Configuration error:\n{e}")
```

### Run Validation Tests

```bash
# Schema validation tests
pytest tests/orchestrator/test_config_validation.py -v

# Health check tests
pytest tests/common/test_config_validator.py -v

# Run all validation tests
pytest tests/orchestrator/test_config_validation.py tests/common/test_config_validator.py -v
```

## Environment Variable Overrides

Environment variables can override configuration values with automatic type coercion:

```bash
export SCRAPY_CONCURRENT_REQUESTS=64
export STAGE1_MAX_DEPTH=5
```

The validation system will:
1. Convert the string values to the correct type
2. Validate the converted values against schema rules
3. Fail with clear error if invalid

## Validation Best Practices

1. **Always run with validation enabled** (default behavior)
2. **Test configuration changes** before deploying to production
3. **Check error messages carefully** - they indicate the exact location and nature of the problem
4. **Use the schema as documentation** - see [src/common/config_schema.py](../src/common/config_schema.py) for all available options
5. **Run validation tests** when modifying the schema

## Disabling Validation (Not Recommended)

While not recommended, you can disable validation:

```python
config = Config(env='development', validate=False)
```

⚠️ **Warning:** Disabling validation may lead to runtime errors that are harder to debug.
