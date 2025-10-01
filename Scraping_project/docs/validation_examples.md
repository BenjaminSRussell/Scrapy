# Configuration Validation Examples

This document shows real examples of validation errors caught by the Pydantic-based validation system.

## Example 1: Typo in Configuration Key (maxDepth instead of max_depth)

### Invalid Configuration
```yaml
stages:
  discovery:
    maxDepth: 5  # ❌ Typo: should be 'max_depth'
```

### Error Output
```
Configuration validation failed:

  ❌ Unknown key 'maxDepth' at stages -> discovery -> maxDepth
     This might be a typo. Check your configuration file.
```

### Fix
```yaml
stages:
  discovery:
    max_depth: 5  # ✅ Correct
```

---

## Example 2: String "5" Instead of Integer 5

### Configuration (Auto-Corrected)
```yaml
stages:
  discovery:
    max_depth: "5"  # String, but will be coerced to int
```

### Result
✅ **Valid** - Pydantic automatically coerces `"5"` to `5`

### Invalid Version
```yaml
stages:
  discovery:
    max_depth: "five"  # ❌ Cannot coerce to int
```

### Error Output
```
Configuration validation failed:

  ❌ Type error at stages -> discovery -> max_depth: Input should be a valid integer, unable to parse string as an integer
     Got: five (type: str)
```

---

## Example 3: Value Out of Range

### Invalid Configuration
```yaml
stages:
  discovery:
    max_depth: 15  # ❌ Must be between 0 and 10
```

### Error Output
```
Configuration validation failed:

  ❌ Value error at stages -> discovery -> max_depth: Input should be less than or equal to 10
```

### Fix
```yaml
stages:
  discovery:
    max_depth: 5  # ✅ Within valid range (0-10)
```

---

## Example 4: Invalid Enum Value

### Invalid Configuration
```yaml
logging:
  level: "TRACE"  # ❌ Not a valid log level
```

### Error Output
```
Configuration validation failed:

  ❌ Type error at logging -> level: Input should be 'DEBUG', 'INFO', 'WARNING', 'ERROR' or 'CRITICAL'
```

### Fix
```yaml
logging:
  level: "INFO"  # ✅ Valid log level
```

---

## Example 5: Concurrent Requests Hierarchy Violation

### Invalid Configuration
```yaml
scrapy:
  concurrent_requests: 10
  concurrent_requests_per_domain: 20  # ❌ Exceeds total
```

### Error Output
```
Configuration validation failed:

  ❌ Value error at scrapy: concurrent_requests_per_domain (20) cannot exceed concurrent_requests (10)
```

### Fix
```yaml
scrapy:
  concurrent_requests: 32
  concurrent_requests_per_domain: 16  # ✅ Less than total
```

---

## Example 6: Invalid Domain Format

### Invalid Configuration
```yaml
stages:
  discovery:
    allowed_domains:
      - "my_domain"  # ❌ Invalid format (no TLD)
```

### Error Output
```
Configuration validation failed:

  ❌ Value error at stages -> discovery -> allowed_domains: Invalid domain format: my_domain
```

### Fix
```yaml
stages:
  discovery:
    allowed_domains:
      - "example.com"  # ✅ Valid domain
```

---

## Example 7: Backpressure Threshold Logic Error

### Invalid Configuration
```yaml
queue:
  backpressure_warning_threshold: 0.95
  backpressure_critical_threshold: 0.80  # ❌ Warning must be < critical
```

### Error Output
```
Configuration validation failed:

  ❌ Value error at queue: backpressure_warning_threshold (0.95) must be less than backpressure_critical_threshold (0.8)
```

### Fix
```yaml
queue:
  backpressure_warning_threshold: 0.80  # ✅ Warning < critical
  backpressure_critical_threshold: 0.95
```

---

## Example 8: Incompatible Browser Engine and Type

### Invalid Configuration
```yaml
stages:
  enrichment:
    headless_browser:
      engine: "selenium"
      browser_type: "webkit"  # ❌ Selenium doesn't support WebKit
```

### Error Output
```
Configuration validation failed:

  ❌ Value error at stages -> enrichment -> headless_browser: Selenium does not support WebKit browser. Use 'chromium', 'firefox', or 'chrome', or switch to 'playwright' engine.
```

### Fix (Option 1)
```yaml
headless_browser:
  engine: "playwright"  # ✅ Playwright supports WebKit
  browser_type: "webkit"
```

### Fix (Option 2)
```yaml
headless_browser:
  engine: "selenium"
  browser_type: "chromium"  # ✅ Selenium supports Chromium
```

---

## Example 9: Negative Value for Positive-Only Field

### Invalid Configuration
```yaml
scrapy:
  concurrent_requests: -5  # ❌ Must be >= 1
```

### Error Output
```
Configuration validation failed:

  ❌ Value error at scrapy -> concurrent_requests: Input should be greater than or equal to 1
```

### Fix
```yaml
scrapy:
  concurrent_requests: 32  # ✅ Positive value
```

---

## Example 10: Invalid MIME Type Format

### Invalid Configuration
```yaml
stages:
  enrichment:
    content_types:
      enabled_types:
        - "not-a-mime-type"  # ❌ Invalid format
```

### Error Output
```
Configuration validation failed:

  ❌ Value error at stages -> enrichment -> content_types -> enabled_types: Invalid MIME type format: not-a-mime-type
```

### Fix
```yaml
stages:
  enrichment:
    content_types:
      enabled_types:
        - "text/html"           # ✅ Valid MIME type
        - "application/pdf"     # ✅ Valid MIME type
```

---

## Example 11: Multiple Errors at Once

### Invalid Configuration
```yaml
stages:
  discovery:
    maxDepth: "fifteen"  # ❌ 1. Typo in key name
                         # ❌ 2. Invalid value type
    max_workers: 150     # ❌ 3. Unknown field (wrong section)
  validation:
    timeout: -10         # ❌ 4. Negative value
```

### Error Output
```
Configuration validation failed:

  ❌ Unknown key 'maxDepth' at stages -> discovery -> maxDepth
     This might be a typo. Check your configuration file.

  ❌ Unknown key 'max_workers' at stages -> discovery -> max_workers
     This might be a typo. Check your configuration file.

  ❌ Value error at stages -> validation -> timeout: Input should be greater than or equal to 1
```

### Fix
```yaml
stages:
  discovery:
    max_depth: 5         # ✅ Correct key name and valid value
  validation:
    max_workers: 16      # ✅ Moved to correct section
    timeout: 15          # ✅ Positive value
```

---

## Testing Your Configuration

### Option 1: Run Validation Tests
```bash
pytest tests/orchestrator/test_config_validation.py -v
```

### Option 2: Test Configuration Programmatically
```python
from src.orchestrator.config import Config, ConfigValidationError

try:
    config = Config(env='development', validate=True)
    print("✅ Configuration is valid!")
except ConfigValidationError as e:
    print(f"❌ Configuration error:\n{e}")
```

### Option 3: Validate Before Running Pipeline
The validation runs automatically when the pipeline starts:
```bash
python -m src.orchestrator.main
```

If configuration is invalid, the pipeline will exit immediately with error details.

---

## Benefits of This Validation System

1. **Catches Typos Early**: Unknown keys are rejected, preventing silent failures
2. **Type Safety**: Automatic type coercion where safe, errors otherwise
3. **Range Validation**: Ensures values are within sensible bounds
4. **Logic Validation**: Cross-field validation (e.g., warning < critical)
5. **Clear Error Messages**: Exact location and nature of each error
6. **Fail-Fast**: Errors are caught before pipeline execution begins
7. **Documentation**: Schema serves as source of truth for valid options

## See Also

- [Configuration Validation Guide](configuration_validation.md) - Complete validation documentation
- [config_schema.py](../src/common/config_schema.py) - Pydantic schema source code
- [test_config_validation.py](../tests/orchestrator/test_config_validation.py) - Validation test suite
