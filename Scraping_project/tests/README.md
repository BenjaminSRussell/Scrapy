# Test Suite Documentation

## Overview

The test suite has been consolidated into a single, focused file that covers all key functionality of the pipeline. This approach reduces maintenance overhead while ensuring comprehensive coverage of critical components.

## Test Structure

### Single Test File: `test_core_functionality.py`

All tests are organized into logical classes based on the component being tested:

1. **TestDeltaLake** - Delta Lake manager tests
2. **TestPostgresManager** - PostgreSQL integration tests
3. **TestStage2Worker** - Stage 2 worker tests
4. **TestStage3Worker** - Stage 3 worker tests
5. **TestDrainLake** - Drain lake utility tests
6. **TestMLErrorAnalyzer** - ML error analyzer tests
7. **TestIntegration** - Integration tests

## Running Tests

### Run All Tests
```bash
pytest tests/ -v
```

### Run Specific Test Class
```bash
pytest tests/test_core_functionality.py::TestDeltaLake -v
```

### Run Specific Test
```bash
pytest tests/test_core_functionality.py::TestDeltaLake::test_write_and_read_data -v
```

### Run with Coverage
```bash
pytest tests/ --cov=src --cov-report=html
open htmlcov/index.html
```

## Test Coverage

### Delta Lake (3 tests)
- ✅ Manager initialization
- ✅ Write and read operations
- ✅ List tables functionality

### PostgreSQL Manager (3 tests)
- ✅ Graceful degradation without credentials
- ⏭️ Initialization with credentials (skipped if psycopg2 not installed)
- ⏭️ Performance metric logging (skipped if psycopg2 not installed)

### Stage 2 Worker (3 tests)
- ✅ Worker initialization
- ✅ HTML analysis with quality control
- ✅ Quality score calculation

### Stage 3 Worker (2 tests)
- ✅ Worker initialization
- ✅ Document deduplication with MinHash LSH

### Drain Lake Utility (1 test)
- ✅ Drain and verify data removal

### ML Error Analyzer (2 tests)
- ✅ URL feature extraction logic
- ✅ Recommendation generation logic

### Integration (1 test)
- ✅ Delta Lake to Stage 2 data flow

## Test Philosophy

### Focus on Key Functions
Tests target critical functionality that, if broken, would cause pipeline failures:
- Data persistence (Delta Lake)
- Quality control (Stage 2 analysis)
- Deduplication (Stage 3 MinHash)
- Configuration (PostgreSQL graceful degradation)

### Minimal Mocking
Tests use real implementations where possible, only mocking:
- Database connections (PostgreSQL)
- Delta Lake paths (for isolation)
- File system operations (for safety)

### Quick Execution
All tests run in < 2 seconds:
```
=================== 13 passed, 2 skipped, 1 warning in 1.26s ===================
```

## Skipped Tests

Tests are skipped automatically when dependencies are missing:

### PostgreSQL Tests
```python
def test_postgres_manager_with_credentials(self):
    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not installed")
```

**Why Skip?** PostgreSQL tests require `psycopg2-binary` which may not be installed in all environments. The pipeline works without PostgreSQL (metrics just won't be tracked).

## Writing New Tests

### Template for New Test Class
```python
class TestNewComponent:
    """Test NewComponent functionality."""

    @pytest.fixture
    def mock_dependency(self):
        """Create mock dependency."""
        mock = MagicMock()
        return mock

    def test_initialization(self, mock_dependency):
        """Test component can initialize."""
        from src.module.component import NewComponent

        component = NewComponent()
        assert component is not None

    def test_key_functionality(self, mock_dependency):
        """Test critical functionality."""
        from src.module.component import NewComponent

        component = NewComponent()
        result = component.do_something()

        assert result == expected_value
```

### Guidelines
1. **One test file per component** - Keep related tests together
2. **Descriptive names** - Test names should explain what they verify
3. **Fixtures for setup** - Use pytest fixtures for repeated setup
4. **Mock external dependencies** - Don't rely on databases, networks, or file system
5. **Fast execution** - Each test should complete in < 100ms

## Continuous Integration

Tests run automatically on every commit via GitHub Actions:

```yaml
# .github/workflows/main.yml
- name: Run tests with coverage
  run: |
    pytest --cov=src --cov-report=term --cov-report=xml tests/
```

## Troubleshooting

### Test Failures

#### "No module named 'psycopg2'"
**Expected** - PostgreSQL tests will be skipped automatically if psycopg2 is not installed.

**To fix (optional):**
```bash
pip install psycopg2-binary
```

#### "AssertionError: assert X == Y"
**Debug:**
```bash
# Run test with full output
pytest tests/test_core_functionality.py::TestClass::test_name -vv -s
```

#### "ImportError: cannot import name 'X'"
**Cause:** Source code structure changed

**Fix:** Update import statements in test file

### Common Issues

#### Old Delta Lake Data
Tests may read old data from previous runs. This is handled by:
- Using unique IDs in test data
- Checking for `>=` instead of exact counts
- Using temp directories where possible

#### Async Tests
Tests that call async functions use `asyncio.run()`:
```python
def test_async_function(self):
    result = asyncio.run(async_function())
    assert result == expected
```

## Maintenance

### When to Update Tests

1. **New Feature Added** - Add test for core functionality
2. **Bug Fixed** - Add regression test
3. **API Changed** - Update affected tests
4. **Refactoring** - Ensure tests still pass

### Test Maintenance Checklist

- [ ] Tests run in < 5 seconds total
- [ ] No hardcoded paths or credentials
- [ ] All critical functions covered
- [ ] Mocks are minimal and necessary
- [ ] Test names are descriptive
- [ ] Comments explain non-obvious logic

## Test Results Summary

**Last Run:** 2025-10-06

```
tests/test_core_functionality.py::TestDeltaLake::test_delta_manager_initialization PASSED
tests/test_core_functionality.py::TestDeltaLake::test_write_and_read_data PASSED
tests/test_core_functionality.py::TestDeltaLake::test_list_tables PASSED
tests/test_core_functionality.py::TestPostgresManager::test_postgres_manager_graceful_degradation PASSED
tests/test_core_functionality.py::TestPostgresManager::test_postgres_manager_with_credentials SKIPPED
tests/test_core_functionality.py::TestPostgresManager::test_log_performance_metric SKIPPED
tests/test_core_functionality.py::TestStage2Worker::test_worker_initialization PASSED
tests/test_core_functionality.py::TestStage2Worker::test_html_analysis PASSED
tests/test_core_functionality.py::TestStage2Worker::test_quality_score_calculation PASSED
tests/test_core_functionality.py::TestStage3Worker::test_worker_initialization PASSED
tests/test_core_functionality.py::TestStage3Worker::test_deduplication PASSED
tests/test_core_functionality.py::TestDrainLake::test_drain_lake_function PASSED
tests/test_core_functionality.py::TestMLErrorAnalyzer::test_url_feature_extraction PASSED
tests/test_core_functionality.py::TestMLErrorAnalyzer::test_recommendation_generation PASSED
tests/test_core_functionality.py::TestIntegration::test_delta_to_stage2_flow PASSED

=================== 13 passed, 2 skipped, 1 warning in 1.26s ===================
```

**Status:** ✅ All critical tests passing
