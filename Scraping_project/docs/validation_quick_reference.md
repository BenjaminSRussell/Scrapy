# Inter-Stage Validation - Quick Reference

## Quick Start

### Validate Pipeline Output (CLI)

```bash
# After pipeline completes
python tools/validate_pipeline_data.py \
  --stage1 data/processed/stage01/discovery_output.jsonl \
  --stage2 data/processed/stage02/validated_urls.jsonl \
  --stage3 data/processed/stage03/enrichment_output.jsonl
```

### Validate in Python

```python
from src.common.interstage_validation import validate_pipeline_output
from pathlib import Path

results = validate_pipeline_output(
    stage1_file=Path('data/processed/stage01/discovery_output.jsonl'),
    stage2_file=Path('data/processed/stage02/validated_urls.jsonl'),
    stage3_file=Path('data/processed/stage03/enrichment_output.jsonl'),
    fail_on_error=False
)

print(f"Overall Status: {results['overall_status']}")
```

## Common Commands

```bash
# Validate single stage
python tools/validate_pipeline_data.py --stage1 <file>

# Validate with failure on errors
python tools/validate_pipeline_data.py --stage1 <file> --fail-on-error

# Fast validation (10% sample)
python tools/validate_pipeline_data.py --stage1 <file> --sample-rate 0.1

# Full pipeline validation
python tools/validate_pipeline_data.py --stage1 <s1> --stage2 <s2> --stage3 <s3>
```

## Validation Checklist

### Stage 1 (Discovery)
- [ ] All records have `source_url` (URL format)
- [ ] All records have `discovered_url` (URL format)
- [ ] All records have `url_hash` (64-char SHA-256)
- [ ] `url_hash` matches `discovered_url`
- [ ] `discovery_depth` is 0-10
- [ ] `confidence` is 0.0-1.0
- [ ] `first_seen` is valid ISO timestamp
- [ ] No extra/unknown fields

### Stage 2 (Validation)
- [ ] All Stage 1 checks pass
- [ ] All `url_hash` values come from Stage 1
- [ ] `status_code` is 0-999
- [ ] `is_valid` field present
- [ ] `error_message` present if `is_valid=False`
- [ ] `validated_at` is valid ISO timestamp
- [ ] Coverage >= 95%

### Stage 3 (Enrichment)
- [ ] All Stage 2 checks pass
- [ ] All `url_hash` values come from Stage 2
- [ ] Only URLs marked `is_valid=True` in Stage 2
- [ ] `word_count` matches `text_content`
- [ ] Required lists present (entities, keywords, tags)
- [ ] `enriched_at` is valid ISO timestamp
- [ ] No orphaned records

## Error Reference

| Error Type | Meaning | Fix |
|------------|---------|-----|
| Field required | Missing required field | Add missing field |
| Extra field not permitted | Unknown field (typo) | Check field name spelling |
| Input should be a valid integer | Type mismatch | Use correct type |
| URL must start with http:// | Invalid URL format | Fix URL format |
| url_hash mismatch | Hash doesn't match URL | Recalculate hash |
| Invalid ISO timestamp | Bad timestamp format | Use ISO format |
| value should be less than X | Out of range | Adjust value to valid range |
| Orphaned hash | Record not from previous stage | Check data pipeline |

## Success Rates

| Rate | Status | Action |
|------|--------|--------|
| >= 95% | ✅ Pass | No action needed |
| 90-94% | ⚠️ Warning | Review errors, may need investigation |
| < 90% | ❌ Fail | Investigate immediately, likely pipeline issue |

## Performance Tips

```python
# For large files (>1M records), use sampling
validator = JSONLValidator('DiscoveryItem', sample_rate=0.1)

# Validate multiple stages in parallel
import concurrent.futures

with concurrent.futures.ThreadPoolExecutor() as executor:
    future1 = executor.submit(validator1.validate_file, stage1_file)
    future2 = executor.submit(validator2.validate_file, stage2_file)
    report1 = future1.result()
    report2 = future2.result()
```

## Quick Debugging

### Find Line with Error

```bash
# If report says "Line 1234: error"
sed -n '1234p' data/processed/stage01/discovery_output.jsonl | python -m json.tool
```

### Count Total Records

```bash
wc -l data/processed/stage01/discovery_output.jsonl
```

### Check for Orphaned Hashes

```python
from src.common.interstage_validation import InterstageValidator
from pathlib import Path

validator = InterstageValidator()
report, stats = validator.validate_stage1_to_stage2(
    Path('data/processed/stage01/discovery_output.jsonl'),
    Path('data/processed/stage02/validated_urls.jsonl')
)

print(f"Orphaned: {stats['extra_in_stage2']}")
```

## Integration Example

```python
# In pipeline code
from src.common.interstage_validation import JSONLValidator

def run_stage1():
    # ... run discovery spider ...

    # Validate output
    validator = JSONLValidator('DiscoveryItem', fail_on_error=True)
    report = validator.validate_file(output_file)

    if not report.is_acceptable:
        raise PipelineError(f"Stage 1 validation failed: {report.invalid_records} errors")

    logger.info(f"Stage 1 validation: {report.success_rate:.2f}% success rate")
```

## Test Validation

```bash
# Run validation tests
pytest tests/common/test_interstage_validation.py -v

# Run specific test
pytest tests/common/test_interstage_validation.py::TestJSONLValidator::test_validate_valid_discovery_file -v
```

## See Also

- [Full Documentation](interstage_validation.md)
- [Implementation Summary](../INTERSTAGE_VALIDATION_SUMMARY.md)
- [schemas_validated.py](../src/common/schemas_validated.py)
- [interstage_validation.py](../src/common/interstage_validation.py)
