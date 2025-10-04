# DevOps Guide: CI/CD and Testing

Complete DevOps guide for the UConn Web Scraping Pipeline.

## Table of Contents

1. [CI/CD Pipeline](#cicd-pipeline)
2. [Testing](#testing)
3. [Deployment](#deployment)
4. [Monitoring](#monitoring)

---

## CI/CD Pipeline

### GitHub Actions Workflow

**File**: `.github/workflows/main.yml`

The pipeline runs on every push and pull request:

```yaml
name: CI/CD Pipeline

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]
```

### Test Job

Runs tests on Python 3.11 and 3.12:

1. **Install dependencies**
   ```bash
   pip install -r Scraping_project/requirements.txt
   python -m spacy download en_core_web_sm
   ```

2. **Lint with ruff**
   ```bash
   ruff check Scraping_project/src/
   ```

3. **Run pytest**
   ```bash
   cd Scraping_project
   python -m pytest tests/ -v
   ```

### Deploy Job

Triggers on push to `main`:

1. Builds Python package
2. Runs deployment script
3. Uses secrets for deployment keys

### Required Secrets

Configure in **GitHub Settings → Secrets → Actions**:

| Secret | Description | Example |
|--------|-------------|---------|
| `DEPLOY_HOST` | Production server hostname | `scraper.example.com` |
| `DEPLOY_USER` | SSH username | `deploy` |
| `DEPLOY_KEY` | SSH private key | `-----BEGIN OPENSSH PRIVATE KEY-----...` |

**Setup SSH Key:**
```bash
# Generate key
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/deploy_key

# Copy public key to server
ssh-copy-id -i ~/.ssh/deploy_key.pub user@server

# Add private key to GitHub Secrets (paste full content)
cat ~/.ssh/deploy_key
```

---

## Testing

### Running Tests Locally

```bash
# All tests
cd Scraping_project
python -m pytest tests/ -v

# Specific test file
python -m pytest tests/common/test_schemas.py -v

# With coverage
python -m pytest tests/ --cov=src --cov-report=html
```

### Test Structure

```
tests/
├── common/              # Common utilities tests
├── stage1/              # Discovery tests
├── stage2/              # Validation tests
├── stage3/              # Enrichment tests
└── integration/         # End-to-end tests
```

### Writing Tests

```python
import pytest
from src.common.url_deduplication import URLDeduplicator

def test_url_deduplicator(tmp_path):
    dedup = URLDeduplicator(tmp_path / "test.db")

    assert dedup.add_if_new("https://example.com")
    assert not dedup.add_if_new("https://example.com")
    assert dedup.count() == 1
```

---

## Deployment

### Manual Deployment

```bash
# Build package
cd Scraping_project
python -m build

# Deploy to server
scp dist/*.whl user@server:/opt/scraper/
ssh user@server "pip install /opt/scraper/*.whl"
```

### Automated Deployment

Triggered on push to `main` branch via GitHub Actions.

Update `.github/workflows/main.yml` with your deployment script:

```yaml
- name: Deploy
  env:
    DEPLOY_KEY: ${{ secrets.DEPLOY_KEY }}
  run: |
    ./scripts/deploy.sh
```

### Deployment Checklist

1. ✅ Tests pass
2. ✅ Code reviewed
3. ✅ Version bumped
4. ✅ Changelog updated
5. ✅ Secrets configured
6. ✅ Deployment script tested

---

## Monitoring

### Logs

**Structured JSON logs** with trace correlation:

```python
from src.common.logging import get_logger
from src.common.log_events import LogEvent

logger = get_logger(__name__)
logger.log_event(LogEvent.URL_DISCOVERED, url=url, depth=1)
```

### Metrics

Key metrics to monitor:

- URLs discovered per minute
- Validation success rate
- NLP processing latency
- Deduplication rate

### Alerting

Set up alerts for:

- Pipeline failures
- High error rates (>5%)
- Memory usage (>80%)
- Disk space (>90%)

---

## Quick Start

1. **Fork repository**
2. **Configure secrets** in GitHub Settings
3. **Push to main** - CI/CD runs automatically
4. **Monitor** GitHub Actions tab

---

## Troubleshooting

### CI Failures

**Q: Linting fails**
```bash
# Fix locally
ruff check --fix Scraping_project/src/
```

**Q: Tests fail**
```bash
# Run locally to debug
python -m pytest tests/ -v --tb=long
```

**Q: Deployment fails**
- Check secrets are configured
- Verify deployment script permissions
- Review GitHub Actions logs

### Local Development

```bash
# Install dev dependencies
pip install -r Scraping_project/requirements.txt
pip install pytest ruff

# Run full CI locally
ruff check Scraping_project/src/
cd Scraping_project && python -m pytest tests/ -v
```

---

## Best Practices

1. **Test before push**: Run tests locally
2. **Small commits**: Easier to debug CI failures
3. **Branch protection**: Require CI to pass before merge
4. **Monitor pipelines**: Check GitHub Actions regularly
5. **Update docs**: Keep this guide current
