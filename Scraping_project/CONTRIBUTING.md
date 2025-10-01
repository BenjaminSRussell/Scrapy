# Contributing to UConn Web Scraping Pipeline

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to the project.

## Code of Conduct

- Be respectful and inclusive
- Welcome newcomers and help them get started
- Focus on what is best for the community
- Show empathy towards other community members

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check existing issues. When creating a bug report, include:

- **Clear title and description**
- **Steps to reproduce** the problem
- **Expected behavior** vs actual behavior
- **Environment details** (OS, Python version, package versions)
- **Relevant logs** or error messages
- **Screenshots** if applicable

Use this template:

```markdown
**Description:**
Brief description of the bug

**To Reproduce:**
1. Step 1
2. Step 2
3. See error

**Expected Behavior:**
What you expected to happen

**Actual Behavior:**
What actually happened

**Environment:**
- OS: [e.g., macOS 13.0]
- Python: [e.g., 3.11.0]
- Package Version: [e.g., 0.2.0]

**Logs/Screenshots:**
```

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When suggesting an enhancement:

- **Use a clear and descriptive title**
- **Provide a detailed description** of the proposed feature
- **Explain why this enhancement would be useful**
- **List any alternative solutions** you've considered
- **Include mockups or examples** if applicable

### Pull Requests

1. **Fork the repository** and create your branch from `main`
2. **Follow the style guidelines** (see below)
3. **Add tests** for new functionality
4. **Update documentation** as needed
5. **Ensure all tests pass** (`make test`)
6. **Run code quality checks** (`make quality`)
7. **Write a clear commit message**

## Development Setup

See [docs/setup.md](docs/setup.md) and [docs/development.md](docs/development.md) for detailed setup instructions.

Quick start:

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/uconn-scraper.git
cd uconn-scraper

# Install with dev dependencies
make install-dev

# Run tests
make test
```

## Style Guidelines

### Python Style

We follow **PEP 8** with these modifications:

- **Line length**: 100 characters (not 79)
- **Quotes**: Prefer double quotes
- **Imports**: Organized with isort
- **Formatting**: Automated with Black

### Code Formatting

Before committing, run:

```bash
# Format code
make format

# Check linting
make lint

# Type checking
make type-check

# Or run all quality checks
make quality
```

### Type Hints

All public functions should have type hints:

```python
from typing import List, Optional, Dict, Any

def process_data(
    input_data: List[str],
    options: Optional[Dict[str, Any]] = None
) -> Dict[str, int]:
    """Process input data and return statistics."""
    pass
```

### Documentation

#### Docstrings

Use Google-style docstrings for all public functions, classes, and modules:

```python
def validate_url(url: str, timeout: int = 30) -> bool:
    """Validate if a URL is accessible.

    Performs a HEAD request to check if the URL returns a successful
    status code (2xx or 3xx).

    Args:
        url: The URL to validate
        timeout: Maximum time to wait in seconds (default: 30)

    Returns:
        True if URL is valid, False otherwise

    Raises:
        ValueError: If URL format is invalid
        TimeoutError: If request exceeds timeout

    Example:
        >>> validate_url("https://example.com")
        True
    """
    pass
```

#### Comments

- Write self-documenting code when possible
- Use comments to explain *why*, not *what*
- Update comments when code changes
- Avoid obvious comments

```python
# Good: Explains why
# Use HEAD first to reduce bandwidth, fallback to GET if needed
response = await session.head(url)

# Bad: States the obvious
# Make a HEAD request
response = await session.head(url)
```

## Testing Guidelines

### Writing Tests

- Write tests for all new features
- Maintain or improve code coverage
- Use descriptive test names
- Follow AAA pattern: Arrange, Act, Assert

```python
def test_url_canonicalization():
    """Test URL canonicalization with various inputs."""
    # Arrange
    input_url = "HTTP://EXAMPLE.COM/PATH"

    # Act
    result = canonicalize_url(input_url)

    # Assert
    assert result == "http://example.com/path"
```

### Test Markers

Use pytest markers to categorize tests:

```python
@pytest.mark.slow
def test_expensive_operation():
    """Test that takes a long time."""
    pass

@pytest.mark.integration
def test_full_pipeline():
    """Test complete pipeline workflow."""
    pass

@pytest.mark.network
def test_with_external_api():
    """Test that requires network access."""
    pass
```

### Running Tests

```bash
# Run all tests
make test

# Run with coverage
make test-cov

# Skip slow tests
make test-fast

# Run specific test
pytest tests/common/test_urls.py::test_canonicalization
```

## Git Commit Guidelines

### Commit Message Format

```
type(scope): subject

body

footer
```

### Types

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Test additions or changes
- `chore`: Build process, tooling, dependencies

### Scope

The scope specifies the area of the codebase:

- `stage1`, `stage2`, `stage3`: Stage-specific changes
- `orchestrator`: Pipeline coordination
- `common`: Shared utilities
- `config`: Configuration
- `docs`: Documentation
- `tests`: Test infrastructure

### Examples

```
feat(stage2): add retry logic for failed HTTP requests

Add exponential backoff retry for network errors in URLValidator.
This improves reliability when validating large batches of URLs.

Closes #42

---

fix(nlp): handle missing spaCy model gracefully

Previously crashed when en_core_web_sm was not installed.
Now falls back to simple keyword extraction.

---

docs(architecture): add system architecture diagram

Add detailed diagram showing data flow between pipeline stages.

---

test(stage1): add tests for dynamic URL discovery

Cover edge cases in JavaScript URL extraction.
```

### Commit Best Practices

- **Atomic commits**: One logical change per commit
- **Clear subject line**: Imperative mood, under 50 characters
- **Detailed body**: Explain *what* and *why*, not *how*
- **Reference issues**: Use "Closes #123" or "Fixes #123"

## Pull Request Process

### Before Submitting

1. ✅ Update your fork:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. ✅ Run tests:
   ```bash
   make test
   ```

3. ✅ Run quality checks:
   ```bash
   make quality
   ```

4. ✅ Update documentation

5. ✅ Add entry to CHANGELOG.md (if applicable)

### PR Description

Use this template:

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix (non-breaking change fixing an issue)
- [ ] New feature (non-breaking change adding functionality)
- [ ] Breaking change (fix or feature causing existing functionality to change)
- [ ] Documentation update

## How Has This Been Tested?
Description of testing approach

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Comments added for complex code
- [ ] Documentation updated
- [ ] Tests added/updated
- [ ] All tests pass
- [ ] No new warnings
```

### Review Process

1. **Automated checks** must pass (tests, linting)
2. **At least one maintainer** must approve
3. **Address review comments** promptly
4. **Squash commits** if requested
5. **Rebase on main** before merge

### After Approval

Maintainers will:
- Squash and merge your PR
- Delete the source branch
- Update project board if applicable

## Development Workflow

### 1. Pick an Issue

- Check [open issues](https://github.com/benjaminrussell/uconn-scraper/issues)
- Comment that you're working on it
- Ask questions if anything is unclear

### 2. Create Branch

```bash
git checkout -b feature/my-feature
```

### 3. Make Changes

```bash
# Make changes
# ...

# Format and check
make format
make lint

# Run tests
make test
```

### 4. Commit

```bash
git add .
git commit -m "feat(scope): clear description"
```

### 5. Push

```bash
git push origin feature/my-feature
```

### 6. Create PR

- Go to GitHub and create Pull Request
- Fill out PR template
- Link related issues
- Request review

### 7. Address Feedback

```bash
# Make requested changes
git add .
git commit -m "address review feedback"
git push origin feature/my-feature
```

## Project Structure

Understanding the codebase:

```
src/
├── common/              # Shared utilities
│   ├── checkpoints.py  # Checkpoint management
│   ├── logging.py      # Logging infrastructure
│   ├── nlp.py          # NLP processing
│   ├── schemas.py      # Data schemas
│   └── urls.py         # URL utilities
├── orchestrator/        # Pipeline coordination
│   ├── config.py       # Configuration management
│   ├── main.py         # Entry point
│   └── pipeline.py     # Stage orchestration
├── stage1/              # Discovery spider
├── stage2/              # URL validation
└── stage3/              # Content enrichment
```

## Common Tasks

### Adding a New Feature

1. Create issue describing the feature
2. Discuss design in issue comments
3. Create feature branch
4. Implement with tests
5. Update documentation
6. Submit PR

### Fixing a Bug

1. Create issue with reproduction steps
2. Write failing test
3. Fix the bug
4. Ensure test passes
5. Submit PR with "Fixes #issue-number"

### Improving Documentation

1. Identify documentation gap
2. Create issue or go straight to PR
3. Update relevant .md files
4. Consider adding examples
5. Submit PR

## Questions?

- Check [docs/](docs/) directory
- Search existing issues
- Create a new issue with the `question` label
- Reach out to maintainers

## Recognition

Contributors will be recognized in:
- README.md contributors section
- CHANGELOG.md for significant contributions
- GitHub contributors page

Thank you for contributing! 🎉
