## Inter-Stage Data Validation

### Overview

The scraping pipeline includes **comprehensive inter-stage validation** to ensure data integrity between pipeline stages. This prevents errors from propagating through the pipeline and catches data quality issues early.

### Key Features

1. **Pydantic Schema Validation** - Strict type checking and field validation
2. **Hash Integrity Checks** - Verify url_hash consistency across stages
3. **Orphan Detection** - Find records that don't trace back to previous stages
4. **Coverage Analysis** - Track what percentage of records flow through
5. **Field Validation** - Ensure required fields present, no extra fields
6. **Type Coercion** - Automatic conversion where safe, errors otherwise
7. **Detailed Reports** - Clear summaries of validation results

### Architecture

```
Stage 1 (Discovery)              Stage 2 (Validation)             Stage 3 (Enrichment)
==================              ====================             ====================
discovered_url + hash     →     url_hash (must match)     →     url_hash (must match)
                                is_valid (must be true)   →     (only valid URLs)

Validation Checks:
1. Schema validation (Pydantic)
2. Hash consistency (SHA-256)
3. Field completeness
4. Inter-stage integrity
```

### Validation Types

#### 1. Schema Validation

Validates each record against Pydantic schemas:

```python
from src.common.schemas_validated import DiscoveryItem

# This will validate:
# - All required fields present
# - No extra/unknown fields
# - Correct types (string, int, float, etc.)
# - Value ranges (depth 0-10, confidence 0.0-1.0)
# - URL format (must start with http:// or https://)
# - Hash format (valid SHA-256 hex)
# - Hash correctness (matches URL)

item = DiscoveryItem(**data)  # Raises ValidationError if invalid
```

**Catches:**
- Missing required fields
- Extra/unknown fields (typos)
- Wrong types (string instead of int)
- Invalid values (depth > 10)
- Malformed URLs
- Incorrect hashes

#### 2. Inter-Stage Integrity

Validates data consistency between stages:

**Stage 1 → Stage 2:**
- All Stage 1 url_hashes should appear in Stage 2
- Stage 2 should not have orphaned hashes
- Coverage tracking (% of URLs validated)

**Stage 2 → Stage 3:**
- All Stage 3 url_hashes must exist in Stage 2
- Stage 3 should only contain URLs marked valid in Stage 2
- No orphaned enrichment records

### Usage

#### Option 1: CLI Tool (Recommended)

```bash
# Validate Stage 1 output
python tools/validate_pipeline_data.py --stage1 data/processed/stage01/discovery_output.jsonl

# Validate Stage 1 → Stage 2
python tools/validate_pipeline_data.py \
  --stage1 data/processed/stage01/discovery_output.jsonl \
  --stage2 data/processed/stage02/validated_urls.jsonl

# Validate full pipeline
python tools/validate_pipeline_data.py \
  --stage1 data/processed/stage01/discovery_output.jsonl \
  --stage2 data/processed/stage02/validated_urls.jsonl \
  --stage3 data/processed/stage03/enrichment_output.jsonl

# Fail on errors (exit code 1)
python tools/validate_pipeline_data.py --stage1 ... --fail-on-error

# Sample validation (faster for large files)
python tools/validate_pipeline_data.py --stage1 ... --sample-rate 0.1  # 10% sample
```

#### Option 2: Python API

```python
from pathlib import Path
from src.common.interstage_validation import (
    JSONLValidator,
    InterstageValidator,
    validate_pipeline_output
)

# Validate single stage
validator = JSONLValidator('DiscoveryItem', fail_on_error=False)
report = validator.validate_file(Path('data/processed/stage01/discovery_output.jsonl'))
print(report.summary())

# Validate inter-stage
validator = InterstageValidator(fail_on_error=False)
report, stats = validator.validate_stage1_to_stage2(stage1_file, stage2_file)

# Validate full pipeline
results = validate_pipeline_output(
    stage1_file=Path('data/processed/stage01/discovery_output.jsonl'),
    stage2_file=Path('data/processed/stage02/validated_urls.jsonl'),
    stage3_file=Path('data/processed/stage03/enrichment_output.jsonl'),
    fail_on_error=False
)
```

### Validation Reports

#### Example: Schema Validation Report

```
============================================================
Inter-Stage Validation Report: DiscoveryItem
============================================================
Total Records: 10,000
Valid Records: 9,850 (98.50%)
Invalid Records: 150

Error Breakdown:
  - Missing Fields: 50
  - Extra Fields: 30
  - Type Errors: 40
  - Value Errors: 30

Sample Errors (5 of 150):
  Line 125: Field required: url_hash
  Line 342: Extra field not permitted: extra_data
  Line 551: Input should be a valid integer for discovery_depth
  Line 892: URL must start with http:// or https://
  Line 1023: url_hash mismatch: expected abc123... got def456...

Status: ✅ PASS - All validation checks passed
============================================================
```

#### Example: Inter-Stage Report

```
============================================================
Inter-Stage Validation Report: Stage1→Stage2
============================================================
Stage 1: 10,000 unique URLs
Stage 2: 9,500 validated URLs
Coverage: 95.00%

Missing in Stage 2: 500 URLs (may be filtered)
Extra in Stage 2: 0 URLs
Orphaned hashes: 0

Status: ✅ PASS
============================================================
```

### Schema Validation Rules

#### DiscoveryItem (Stage 1)

| Field | Type | Validation |
|-------|------|------------|
| source_url | str | Required, must start with http:// or https:// |
| discovered_url | str | Required, must start with http:// or https:// |
| first_seen | str | Required, valid ISO timestamp |
| url_hash | str | Required, 64-char SHA-256 hex, must match discovered_url |
| discovery_depth | int | Required, 0-10 |
| discovery_source | str | Default "html_link" |
| confidence | float | Default 1.0, range 0.0-1.0 |
| schema_version | str | Default "2.0" |

**Extra fields:** Rejected (catches typos)

#### ValidationResult (Stage 2)

| Field | Type | Validation |
|-------|------|------------|
| url | str | Required, must start with http:// or https:// |
| url_hash | str | Required, 64-char SHA-256 hex |
| status_code | int | Required, 0-999 |
| content_type | str | Required |
| content_length | int | Required, >= 0 |
| response_time | float | Required, >= 0.0 |
| is_valid | bool | Required |
| error_message | str | Required if is_valid=False |
| validated_at | str | Required, valid ISO timestamp |
| schema_version | str | Default "2.0" |

**Cross-field validation:**
- If `is_valid=False`, `error_message` must be present

#### EnrichmentItem (Stage 3)

| Field | Type | Validation |
|-------|------|------------|
| url | str | Required, must start with http:// or https:// |
| url_hash | str | Required, 64-char SHA-256 hex |
| title | str | Required |
| text_content | str | Required |
| word_count | int | Required, >= 0, must match text_content |
| entities | list[str] | Required |
| keywords | list[str] | Required |
| content_tags | list[str] | Required |
| has_pdf_links | bool | Required |
| has_audio_links | bool | Required |
| status_code | int | Required, 0-999 |
| content_type | str | Required |
| enriched_at | str | Required, valid ISO timestamp |

**Cross-field validation:**
- `word_count` must match `text_content.split()` (within tolerance of 10)

### Error Handling

#### Development Mode (Fail-Fast)

```python
# Fail immediately on validation errors
validator = JSONLValidator('DiscoveryItem', fail_on_error=True)
try:
    report = validator.validate_file(file_path)
except ValidationFailure as e:
    print(f"Validation failed: {e}")
    sys.exit(1)
```

#### Production Mode (Continue with Warnings)

```python
# Log errors but continue processing
validator = JSONLValidator('DiscoveryItem', fail_on_error=False)
report = validator.validate_file(file_path)

if not report.is_acceptable:  # < 95% success rate
    logger.warning(f"Validation issues detected: {report.invalid_records} errors")
    # Send alert, but continue
```

### Integration with Pipeline

The validation can be integrated at multiple points:

#### 1. Post-Stage Validation

```python
# After Stage 1 completes
validator = JSONLValidator('DiscoveryItem')
report = validator.validate_file(stage1_output)
if not report.is_acceptable:
    raise PipelineError("Stage 1 output validation failed")
```

#### 2. Pre-Stage Validation

```python
# Before Stage 2 starts
interstage = InterstageValidator()
report, stats = interstage.validate_stage1_to_stage2(stage1_file, stage2_file)
if stats['extra_in_stage2'] > 0:
    raise PipelineError(f"Found {stats['extra_in_stage2']} orphaned hashes")
```

#### 3. Continuous Monitoring

```python
# Sample validation during processing
validator = JSONLValidator('DiscoveryItem', sample_rate=0.01)  # 1% sample
report = validator.validate_file(file_path)
# Low overhead, catches issues early
```

### Common Validation Errors

#### Missing Required Field

```json
// ❌ INVALID - missing url_hash
{
  "source_url": "https://example.com",
  "discovered_url": "https://example.com/page",
  "first_seen": "2025-10-01T12:00:00",
  "discovery_depth": 1
}
```

**Error:** `Field required: url_hash`

#### Extra Field (Typo)

```json
// ❌ INVALID - typo in field name
{
  "source_url": "https://example.com",
  "discovered_url": "https://example.com/page",
  "first_seen": "2025-10-01T12:00:00",
  "url_hash": "abc123...",
  "discovery_depth": 1,
  "discoverysource": "html_link"  // Should be "discovery_source"
}
```

**Error:** `Extra field not permitted: discoverysource`

#### Type Error

```json
// ❌ INVALID - depth should be int, not string
{
  "source_url": "https://example.com",
  "discovered_url": "https://example.com/page",
  "first_seen": "2025-10-01T12:00:00",
  "url_hash": "abc123...",
  "discovery_depth": "1"  // String instead of int (will be coerced)
}
```

**Note:** Pydantic will coerce `"1"` to `1`, but `"one"` would fail

#### Hash Mismatch

```json
// ❌ INVALID - url_hash doesn't match discovered_url
{
  "source_url": "https://example.com",
  "discovered_url": "https://example.com/page",
  "first_seen": "2025-10-01T12:00:00",
  "url_hash": "wrong_hash_here",
  "discovery_depth": 1
}
```

**Error:** `url_hash mismatch: expected <correct_hash> for <url>, got wrong_hash_here`

#### Orphaned Record (Inter-Stage)

```
Stage 2 contains url_hash "xyz789..." but this hash was not found in Stage 1
```

**Error:** `Orphaned hash detected in Stage 2`

### Testing

Run validation tests:

```bash
pytest tests/common/test_interstage_validation.py -v
```

Test coverage includes:
- Schema validation for all stages
- Hash integrity checks
- Missing/extra field detection
- Type error detection
- Inter-stage orphan detection
- Coverage calculation

### Performance Considerations

#### Large Files

For large JSONL files (>1M records), use sampling:

```python
# Validate 10% sample (much faster)
validator = JSONLValidator('DiscoveryItem', sample_rate=0.1)
report = validator.validate_file(large_file)
```

#### Parallel Validation

Validate multiple stages concurrently:

```python
import concurrent.futures

with concurrent.futures.ThreadPoolExecutor() as executor:
    future1 = executor.submit(validator1.validate_file, stage1_file)
    future2 = executor.submit(validator2.validate_file, stage2_file)
    future3 = executor.submit(validator3.validate_file, stage3_file)

    report1 = future1.result()
    report2 = future2.result()
    report3 = future3.result()
```

### Best Practices

1. **Validate After Each Stage** - Catch errors immediately
2. **Use Fail-Fast in Development** - Quick feedback loop
3. **Log Warnings in Production** - Continue with alerts
4. **Sample Large Files** - Balance speed vs coverage
5. **Monitor Coverage** - Ensure >95% success rate
6. **Review Error Patterns** - Fix systematic issues
7. **Automate Validation** - Run as part of CI/CD

### Troubleshooting

#### High Invalid Rate

If validation success rate < 95%:

1. Check for systematic errors (same error repeated)
2. Review schema changes (field renamed?)
3. Check data pipeline (bug in spider?)
4. Validate input data quality

#### Orphaned Records

If inter-stage validation finds orphans:

1. Check for concurrent pipeline runs
2. Verify file paths are correct
3. Look for partial writes
4. Check for manual edits to JSONL files

#### Performance Issues

If validation is slow:

1. Use sampling (`sample_rate=0.1`)
2. Validate in parallel
3. Increase worker count
4. Use faster storage (SSD)

### See Also

- [schemas_validated.py](../src/common/schemas_validated.py) - Pydantic schema definitions
- [interstage_validation.py](../src/common/interstage_validation.py) - Validation implementation
- [validate_pipeline_data.py](../tools/validate_pipeline_data.py) - CLI tool
- [test_interstage_validation.py](../tests/common/test_interstage_validation.py) - Test suite
