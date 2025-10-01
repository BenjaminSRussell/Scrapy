# Development Guide

This guide covers setting up a development environment, code standards, testing practices, and contribution workflows for the UConn Web Scraping Pipeline.

## Development Environment Setup

### 1. Fork and Clone

```bash
# Fork the repository on GitHub first, then:
git clone https://github.com/YOUR_USERNAME/uconn-scraper.git
cd uconn-scraper

# Add upstream remote
git remote add upstream https://github.com/benjaminrussell/uconn-scraper.git
```

### 2. Install Development Dependencies

```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install with dev dependencies
pip install -e ".[dev]"
python -m spacy download en_core_web_sm

# Install pre-commit hooks
pre-commit install
```

Or use make:
```bash
make install-dev
```

### 3. Verify Setup

```bash
# Run tests
pytest

# Check code formatting
black --check src tests

# Run linter
flake8 src tests

# Type checking
mypy src
```

Or use make:
```bash
make test
make quality
```

## Project Structure

```
uconn-scraper/
├── src/                      # Source code
│   ├── common/              # Shared utilities
│   ├── orchestrator/        # Pipeline coordination
│   ├── stage1/              # Discovery spider
│   ├── stage2/              # URL validation
│   └── stage3/              # Content enrichment
├── tests/                    # Test suite
│   ├── common/
│   ├── stage1/
│   ├── stage2/
│   ├── stage3/
│   ├── data/                # Test fixtures
│   ├── output/              # Test outputs
│   └── reports/             # Test reports
├── config/                   # Configuration files
├── docs/                     # Documentation
├── data/                     # Runtime data (gitignored)
└── tools/                    # Development scripts
```

## Code Standards

### Style Guide

We follow PEP 8 with some modifications:

- **Line length**: 100 characters (not 79)
- **Quotes**: Prefer double quotes for strings
- **Imports**: Organized with isort
- **Formatting**: Automated with Black

### Type Hints

Use type hints for all public functions:

```python
from typing import List, Optional, Dict, Any

def process_urls(
    urls: List[str],
    timeout: int = 30,
    headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Process a list of URLs.

    Args:
        urls: List of URLs to process
        timeout: Request timeout in seconds
        headers: Optional HTTP headers

    Returns:
        Dictionary with processing results

    Raises:
        ValueError: If urls list is empty
    """
    pass
```

### Docstrings

Use Google-style docstrings:

```python
def validate_url(url: str, timeout: int) -> bool:
    """Validate if a URL is accessible.

    This function performs a HEAD request followed by GET if needed
    to determine if the URL returns a successful status code.

    Args:
        url: The URL to validate
        timeout: Maximum time to wait for response in seconds

    Returns:
        True if URL is valid and accessible, False otherwise

    Raises:
        ValueError: If URL format is invalid
        TimeoutError: If request exceeds timeout

    Example:
        >>> validate_url("https://example.com", timeout=10)
        True
    """
    pass
```

### Error Handling

1. **Be specific with exceptions**:
   ```python
   # Good
   try:
       data = json.loads(text)
   except json.JSONDecodeError as e:
       logger.error(f"Invalid JSON: {e}", exc_info=True)

   # Bad
   try:
       data = json.loads(text)
   except Exception:
       pass
   ```

2. **Use exc_info for debugging**:
   ```python
   logger.error("Failed to process item", exc_info=True)
   ```

3. **Provide context**:
   ```python
   raise ValueError(f"Invalid URL format: {url}") from e
   ```

### Logging

Use structured logging:

```python
import logging
from src.common.logging import get_structured_logger

# Module-level logger
logger = logging.getLogger(__name__)

# Structured logger with context
logger = get_structured_logger(__name__, component="validator")

# Log with context
logger.info("Processing batch", batch_size=len(batch), worker_id=worker_id)
```

## Testing

### Test Organization

```python
# tests/stage2/test_validator.py
import pytest
from src.stage2.validator import URLValidator

class TestURLValidator:
    """Tests for URL validation logic."""

    @pytest.fixture
    def validator(self):
        """Create validator instance for testing."""
        from src.orchestrator.config import Config
        config = Config('development')
        return URLValidator(config)

    def test_valid_url(self, validator):
        """Test validation of a valid URL."""
        result = await validator.validate_url("https://example.com")
        assert result.is_valid

    @pytest.mark.slow
    def test_slow_operation(self):
        """Test that takes a long time."""
        pass

    @pytest.mark.network
    def test_requires_network(self):
        """Test that requires network access."""
        pass
```

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/stage2/test_validator.py

# Run specific test
pytest tests/stage2/test_validator.py::TestURLValidator::test_valid_url

# Run with coverage
pytest --cov=src --cov-report=html

# Skip slow tests
pytest -m "not slow"

# Run only integration tests
pytest -m integration

# Verbose output
pytest -v

# Stop on first failure
pytest -x
```

Using make:
```bash
make test          # All tests
make test-cov      # With coverage
make test-fast     # Skip slow tests
make test-integration  # Integration only
```

### Writing Tests

#### Unit Tests

Test individual functions in isolation:

```python
def test_canonicalize_url():
    """Test URL canonicalization."""
    from src.common.urls import canonicalize_url_simple

    # Test basic canonicalization
    assert canonicalize_url_simple("HTTP://EXAMPLE.COM") == "http://example.com"

    # Test path normalization
    assert canonicalize_url_simple("http://example.com/a/../b") == "http://example.com/b"

    # Test query parameter ordering
    url = "http://example.com?b=2&a=1"
    result = canonicalize_url_simple(url)
    assert "a=1" in result and "b=2" in result
```

#### Integration Tests

Test component interactions:

```python
@pytest.mark.integration
async def test_full_validation_flow():
    """Test complete validation workflow."""
    config = Config('development')
    orchestrator = PipelineOrchestrator(config)
    validator = URLValidator(config)

    # Stage 1 → Stage 2 flow
    await orchestrator.populate_stage2_queue()
    await orchestrator.run_concurrent_stage2_validation(validator)

    # Verify output
    output_file = Path("data/processed/stage02/validation_output.jsonl")
    assert output_file.exists()
```

#### Mocking

Use pytest-mock for external dependencies:

```python
def test_with_mock_http(mocker):
    """Test with mocked HTTP calls."""
    mock_response = mocker.Mock()
    mock_response.status = 200
    mock_response.headers = {"content-type": "text/html"}

    mocker.patch('aiohttp.ClientSession.get', return_value=mock_response)

    # Test code that uses aiohttp
    result = await fetch_url("https://example.com")
    assert result.status == 200
```

### Test Fixtures

Create reusable test data:

```python
# tests/conftest.py
import pytest
from pathlib import Path

@pytest.fixture
def sample_urls():
    """Provide sample URLs for testing."""
    return [
        "https://uconn.edu",
        "https://catalog.uconn.edu",
        "https://admissions.uconn.edu"
    ]

@pytest.fixture
def temp_output_dir(tmp_path):
    """Provide temporary output directory."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir

@pytest.fixture
def mock_config(mocker):
    """Provide mocked configuration."""
    config = mocker.Mock()
    config.get_stage2_config.return_value = {
        'max_workers': 4,
        'timeout': 10,
        'output_file': 'test_output.jsonl'
    }
    return config
```

## Code Quality Tools

### Black (Code Formatting)

```bash
# Format all code
black src tests

# Check without modifying
black --check src tests

# Format specific file
black src/common/urls.py
```

Or use make:
```bash
make format
```

### isort (Import Sorting)

```bash
# Sort imports
isort src tests

# Check without modifying
isort --check src tests
```

### Flake8 (Linting)

```bash
# Run linter
flake8 src tests --max-line-length=100

# Ignore specific errors
flake8 src tests --extend-ignore=E203,W503
```

Or use make:
```bash
make lint
```

### mypy (Type Checking)

```bash
# Check types
mypy src

# Check specific module
mypy src/common/urls.py
```

Or use make:
```bash
make type-check
```

### Pre-commit Hooks

Hooks run automatically on `git commit`:

```bash
# Install hooks
pre-commit install

# Run manually on all files
pre-commit run --all-files

# Run on staged files only
pre-commit run
```

Configuration in `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 24.1.0
    hooks:
      - id: black

  - repo: https://github.com/PyCQA/isort
    rev: 5.13.0
    hooks:
      - id: isort

  - repo: https://github.com/PyCQA/flake8
    rev: 7.0.0
    hooks:
      - id: flake8
        args: [--max-line-length=100]
```

## Git Workflow

### Branch Naming

- `feature/description` - New features
- `fix/description` - Bug fixes
- `docs/description` - Documentation
- `refactor/description` - Code refactoring
- `test/description` - Test improvements

### Commit Messages

Follow conventional commits:

```
type(scope): subject

body (optional)

footer (optional)
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Formatting changes
- `refactor`: Code restructuring
- `test`: Test additions/changes
- `chore`: Build process or auxiliary tool changes

Examples:
```
feat(stage2): add retry logic for failed validations

fix(nlp): handle missing spaCy model gracefully

docs(architecture): add system architecture diagram

test(stage1): add tests for dynamic URL discovery
```

### Pull Request Process

1. **Create feature branch**:
   ```bash
   git checkout -b feature/my-feature
   ```

2. **Make changes and commit**:
   ```bash
   git add .
   git commit -m "feat(scope): description"
   ```

3. **Keep up to date**:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

4. **Push to your fork**:
   ```bash
   git push origin feature/my-feature
   ```

5. **Create Pull Request on GitHub**

6. **Address review comments**

7. **Squash and merge** when approved

## Debugging

### Using Python Debugger

```python
# Add breakpoint
import pdb; pdb.set_trace()

# Or use built-in breakpoint() (Python 3.7+)
breakpoint()
```

### Logging for Debugging

```python
# Temporary debug logging
import logging
logging.basicConfig(level=logging.DEBUG)

# Or set via environment
DEBUG=1 python -m src.orchestrator.main --stage 1
```

### Scrapy Debugging

```python
# In spider
from scrapy.shell import inspect_response

def parse(self, response):
    inspect_response(response, self)
    # This opens interactive shell
```

### pytest Debugging

```bash
# Drop into debugger on failure
pytest --pdb

# Drop into debugger at start
pytest --trace

# Show print statements
pytest -s

# Verbose output
pytest -vv
```

## Performance Profiling

### Using cProfile

```bash
python -m cProfile -o profile.stats -m src.orchestrator.main --stage 2

# Analyze results
python -m pstats profile.stats
>>> sort cumulative
>>> stats 20
```

### Memory Profiling

```bash
# Install memory profiler
pip install memory-profiler

# Profile function
@profile
def my_function():
    pass

# Run profiler
python -m memory_profiler script.py
```

## Documentation

### Update Documentation

When adding features:
1. Update relevant `.md` files in `docs/`
2. Add docstrings to all public functions/classes
3. Update README.md if user-facing
4. Add examples for complex features

### Generate API Documentation (Future)

```bash
# Using pdoc
pip install pdoc3
pdoc --html --output-dir docs/api src/
```

## Release Process

1. Update version in `pyproject.toml`
2. Update CHANGELOG.md
3. Create git tag:
   ```bash
   git tag -a v0.2.0 -m "Release version 0.2.0"
   git push upstream v0.2.0
   ```
4. Build and publish (maintainers only):
   ```bash
   python -m build
   twine upload dist/*
   ```

## Getting Help

- Check existing issues on GitHub
- Review architecture documentation
- Ask questions in pull requests
- Reach out to maintainers
