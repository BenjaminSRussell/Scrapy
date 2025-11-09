# Test Suite

Comprehensive test suite for the UConn Scraping Pipeline.

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_cache.py

# Run with verbose output
pytest -v

# Run async tests only
pytest -k async
```

## Test Structure

- `conftest.py` - Shared fixtures and configuration
- `test_cache.py` - Cache layer tests
- `test_retry.py` - Retry and circuit breaker tests
- `test_models.py` - Pydantic model validation tests

## Coverage Goals

- Target: 80%+ code coverage
- Critical paths: 95%+ coverage
- Integration tests for all pipeline stages

## Writing Tests

Use provided fixtures from conftest.py:

```python
@pytest.mark.asyncio
async def test_example(mock_redis, sample_url_record):
    # Your test here
    pass
```
