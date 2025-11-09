# 5-Phase Code Reorganization Plan

## Executive Summary

This document outlines a comprehensive 5-phase plan to reorganize the UConn scraping pipeline codebase with the following goals:

1. **Better organization**: No folder should have more than 4 files
2. **Global helper functions**: Create centralized utility modules
3. **Logical helper usage**: Refactor for DRY principles
4. **Thorough code review**: Identify and fix all issues
5. **Remove old code**: Clean up unused/deprecated code

---

## Phase 1: Code Audit & Issue Identification

**Duration**: 1-2 days
**Goal**: Comprehensive review of all files, identify issues, document findings

### Tasks

#### 1.1 File Count Audit
- Count files in each directory
- Identify directories with >4 files
- Create reorganization plan for each

#### 1.2 Code Review - Core Pipeline
Review each file for:
- Unused imports
- Duplicate code
- Missing error handling
- Inconsistent naming
- Dead code
- TODO/FIXME comments
- Missing type hints
- Security issues

**Files to Review:**
```
src/stage1/
  - spiders/scout_spider.py
  - spiders/__init__.py
  - middlewares.py
  - __init__.py
  (4 files - OK)

src/stage2/
  - stage2_worker.py
  - __init__.py
  (2 files - OK)

src/stage3/
  - stage3_worker.py
  - __init__.py
  (2 files - OK)

src/stage4/
  - stage4_worker.py
  - large_doc_processor.py
  - __init__.py
  (3 files - OK)

src/common/ ⚠️ NEEDS REVIEW
  - storage_manager.py
  - redis_client.py
  - logger.py
  - metrics_manager.py
  - delta_lake.py (possibly unused)
  - __init__.py
  (6 files - OVER LIMIT)

src/lakehouse/
  - lakehouse_manager.py
  - __init__.py
  (2 files - OK)

src/orchestrator/
  - pipeline_orchestrator.py
  - __init__.py
  (2 files - OK)

src/analytics/
  - deduplication_service.py
  - summarizer.py
  - __init__.py
  (3 files - OK)
```

#### 1.3 Identify Duplicate Code
Scan for repeated patterns:
- Delta Lake read/write operations
- Redis connection management
- Metrics collection
- Logging setup
- Error handling
- URL validation
- File I/O operations

#### 1.4 Document Current Issues
Create comprehensive issue list:
- Bug reports
- Code smells
- Architecture issues
- Performance bottlenecks
- Security concerns
- Technical debt

### Deliverables
- `CODE_AUDIT_REPORT.md` - Comprehensive findings
- `ISSUES_LIST.md` - All identified issues
- `REFACTOR_PLAN.md` - Detailed refactoring plan

---

## Phase 2: Create Global Helper Functions

**Duration**: 2-3 days
**Goal**: Extract common functionality into centralized utility modules

### New Structure

```
src/utils/
  ├── __init__.py
  ├── delta.py          # Delta Lake helpers
  ├── redis.py          # Redis helpers
  ├── metrics.py        # Metrics helpers
  └── validation.py     # Input validation helpers

src/core/
  ├── __init__.py
  ├── config.py         # Configuration management
  ├── logging.py        # Logging setup
  └── exceptions.py     # Custom exceptions

src/helpers/
  ├── __init__.py
  ├── text.py           # Text processing helpers
  ├── url.py            # URL manipulation helpers
  └── time.py           # Time/date helpers
```

### 2.1 Delta Lake Utilities (`src/utils/delta.py`)

```python
"""
Global utilities for Delta Lake operations.
Centralizes all Delta Lake read/write/update operations.
"""

from typing import List, Dict, Any, Optional
from pathlib import Path
from deltalake import DeltaTable

class DeltaHelper:
    """Centralized Delta Lake operations."""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self._manager = None

    @property
    def manager(self):
        if self._manager is None:
            from src.lakehouse import LakehouseManager
            self._manager = LakehouseManager(self.base_path)
        return self._manager

    def read_table(self, table_name: str,
                   filters: Optional[List] = None) -> List[Dict]:
        """Read from Delta table with optional filters."""
        pass

    def write_table(self, table_name: str, data: List[Dict],
                    mode: str = "append") -> None:
        """Write to Delta table."""
        pass

    def update_table(self, table_name: str,
                     predicate: str,
                     updates: Dict[str, Any]) -> None:
        """Update records in Delta table."""
        pass

    def table_exists(self, table_name: str) -> bool:
        """Check if table exists."""
        pass

    def get_row_count(self, table_name: str) -> int:
        """Get total row count."""
        pass

    def compact_table(self, table_name: str) -> None:
        """Compact small files."""
        pass

# Global instance
_delta_helper: Optional[DeltaHelper] = None

def get_delta() -> DeltaHelper:
    """Get global Delta helper instance."""
    global _delta_helper
    if _delta_helper is None:
        from src.lakehouse import get_data_dir
        _delta_helper = DeltaHelper(get_data_dir())
    return _delta_helper
```

### 2.2 Redis Utilities (`src/utils/redis.py`)

```python
"""
Global utilities for Redis operations.
Centralizes connection management and common operations.
"""

from typing import Optional, List, Set, Dict, Any
import redis
from functools import wraps

class RedisHelper:
    """Centralized Redis operations."""

    def __init__(self, host: str = "localhost", port: int = 6379):
        self.host = host
        self.port = port
        self._client: Optional[redis.Redis] = None

    @property
    def client(self) -> redis.Redis:
        """Lazy connection."""
        if self._client is None:
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                decode_responses=True
            )
        return self._client

    def check_url_seen(self, url: str, key_prefix: str = "seen") -> bool:
        """Check if URL has been seen."""
        pass

    def mark_url_seen(self, url: str, key_prefix: str = "seen") -> None:
        """Mark URL as seen."""
        pass

    def add_to_set(self, key: str, *values: str) -> int:
        """Add values to Redis set."""
        pass

    def get_set_size(self, key: str) -> int:
        """Get size of Redis set."""
        pass

    def increment_counter(self, key: str, amount: int = 1) -> int:
        """Increment counter."""
        pass

    def get_memory_usage(self) -> int:
        """Get Redis memory usage in bytes."""
        pass

# Global instance
_redis_helper: Optional[RedisHelper] = None

def get_redis() -> RedisHelper:
    """Get global Redis helper instance."""
    global _redis_helper
    if _redis_helper is None:
        _redis_helper = RedisHelper()
    return _redis_helper

def redis_retry(max_attempts: int = 3):
    """Decorator for Redis operations with retry."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except redis.ConnectionError:
                    if attempt == max_attempts - 1:
                        raise
                    time.sleep(2 ** attempt)
        return wrapper
    return decorator
```

### 2.3 Metrics Utilities (`src/utils/metrics.py`)

```python
"""
Global utilities for metrics collection.
Centralizes Prometheus metrics management.
"""

from typing import Dict, Optional
from prometheus_client import Counter, Gauge, Histogram, Summary

class MetricsHelper:
    """Centralized metrics management."""

    def __init__(self):
        self._metrics: Dict[str, Any] = {}
        self._initialized = False

    def initialize_stage_metrics(self, stage: int) -> None:
        """Initialize metrics for a specific stage."""
        pass

    def increment_counter(self, name: str, amount: float = 1) -> None:
        """Increment a counter metric."""
        pass

    def set_gauge(self, name: str, value: float) -> None:
        """Set a gauge metric."""
        pass

    def record_timing(self, name: str, value: float) -> None:
        """Record timing metric."""
        pass

# Global instance
_metrics_helper: Optional[MetricsHelper] = None

def get_metrics() -> MetricsHelper:
    """Get global metrics helper instance."""
    global _metrics_helper
    if _metrics_helper is None:
        _metrics_helper = MetricsHelper()
    return _metrics_helper
```

### 2.4 Validation Utilities (`src/utils/validation.py`)

```python
"""
Global input validation utilities.
"""

from typing import Optional
from urllib.parse import urlparse
import re

def is_valid_url(url: str) -> bool:
    """Validate URL format."""
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

def is_uconn_domain(url: str) -> bool:
    """Check if URL is UConn domain."""
    parsed = urlparse(url)
    return 'uconn.edu' in parsed.netloc.lower()

def sanitize_text(text: str, max_length: Optional[int] = None) -> str:
    """Sanitize text input."""
    text = re.sub(r'\s+', ' ', text).strip()
    if max_length and len(text) > max_length:
        text = text[:max_length]
    return text

def validate_stage_data(data: dict, required_fields: list) -> bool:
    """Validate stage data has required fields."""
    return all(field in data for field in required_fields)
```

### 2.5 Core Configuration (`src/core/config.py`)

```python
"""
Global configuration management.
"""

from pathlib import Path
from typing import Optional
import yaml

class Config:
    """Global configuration."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or Path("config/config.yml")
        self._config: Optional[dict] = None

    @property
    def config(self) -> dict:
        if self._config is None:
            self.load()
        return self._config

    def load(self) -> None:
        """Load configuration from YAML."""
        if self.config_path.exists():
            with open(self.config_path) as f:
                self._config = yaml.safe_load(f)
        else:
            self._config = self._default_config()

    def _default_config(self) -> dict:
        """Default configuration."""
        return {
            "redis": {"host": "localhost", "port": 6379},
            "delta_lake": {"base_path": "./data/delta_lake"},
            "stages": {
                "stage1": {"url_limit": 100},
                "stage2": {"concurrent": 50},
                "stage3": {"concurrent": 20},
                "stage4": {"enabled": True}
            }
        }

    def get(self, key: str, default=None):
        """Get config value by dot notation key."""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default
        return value if value is not None else default

# Global instance
_config: Optional[Config] = None

def get_config() -> Config:
    """Get global config instance."""
    global _config
    if _config is None:
        _config = Config()
    return _config
```

### 2.6 Core Logging (`src/core/logging.py`)

```python
"""
Global logging configuration.
"""

import logging
import sys
from pathlib import Path
from typing import Optional

def setup_logging(
    name: str,
    level: str = "INFO",
    log_file: Optional[Path] = None
) -> logging.Logger:
    """Set up standardized logging."""

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
```

### 2.7 Custom Exceptions (`src/core/exceptions.py`)

```python
"""
Custom exceptions for the pipeline.
"""

class PipelineException(Exception):
    """Base exception for pipeline errors."""
    pass

class DeltaTableError(PipelineException):
    """Delta Lake operation failed."""
    pass

class RedisConnectionError(PipelineException):
    """Redis connection failed."""
    pass

class ValidationError(PipelineException):
    """Input validation failed."""
    pass

class StageProcessingError(PipelineException):
    """Stage processing failed."""
    pass

class ConfigurationError(PipelineException):
    """Configuration error."""
    pass
```

### Deliverables
- New `src/utils/` directory with 4 utility modules
- New `src/core/` directory with 3 core modules
- New `src/helpers/` directory with 3 helper modules
- All modules fully documented with docstrings
- Unit tests for each utility function

---

## Phase 3: Refactor Existing Code

**Duration**: 3-4 days
**Goal**: Refactor all existing code to use new global helpers

### 3.1 Refactor src/common/

**Current state**: 6 files (OVER LIMIT)

**Action**: Break down and redistribute

```
Before:
src/common/
  - storage_manager.py (move to src/utils/delta.py)
  - redis_client.py (move to src/utils/redis.py)
  - logger.py (move to src/core/logging.py)
  - metrics_manager.py (move to src/utils/metrics.py)
  - delta_lake.py (DELETE if unused, or merge)
  - __init__.py

After:
src/common/
  - __init__.py (re-exports from utils/core)
  (1 file - OK)
```

### 3.2 Refactor Stage Workers

**Before** (stage2_worker.py example):
```python
from src.common.storage_manager import get_delta_manager
from src.common.redis_client import RedisClient
from src.common.logger import setup_logger

delta = get_delta_manager()
redis = RedisClient()
logger = setup_logger(__name__)

# ... lots of repeated code
```

**After** (stage2_worker.py):
```python
from src.utils.delta import get_delta
from src.utils.redis import get_redis
from src.core.logging import setup_logging

delta = get_delta()
redis = get_redis()
logger = setup_logging(__name__)

# ... cleaner code with utilities
```

### 3.3 Remove Duplicate Code

Identify and extract:
- Delta Lake read/write patterns → `utils.delta`
- URL validation → `utils.validation`
- Redis operations → `utils.redis`
- Metrics collection → `utils.metrics`
- Text processing → `helpers.text`

### 3.4 Update All Imports

Run global search-and-replace:
```bash
# Old imports
from src.common.storage_manager import get_delta_manager
from src.common.redis_client import RedisClient
from src.common.logger import setup_logger

# New imports
from src.utils.delta import get_delta
from src.utils.redis import get_redis
from src.core.logging import setup_logging
```

### Deliverables
- Refactored stage workers
- Updated orchestrator
- Updated analytics modules
- All imports updated
- All tests passing

---

## Phase 4: Code Review & Issue Resolution

**Duration**: 2-3 days
**Goal**: Fix all identified issues, remove old code

### 4.1 Fix Identified Issues

From Phase 1 audit, systematically fix:

#### Priority 1: Critical Issues
- Security vulnerabilities
- Data loss risks
- Memory leaks
- Infinite loops

#### Priority 2: Major Issues
- Error handling gaps
- Race conditions
- Resource leaks
- Performance bottlenecks

#### Priority 3: Minor Issues
- Code style inconsistencies
- Missing type hints
- Incomplete docstrings
- Unused imports

### 4.2 Remove Old/Unused Code

**Delete**:
```
temp_scripts/ (except latest test files)
  - old_test_*.py
  - cleanup_*.py
  - run_*.py (consolidate into one)

monitoring/ (if Grafana dashboards not used)
  - *.json (if empty/unused)

kafka-delta-ingest/ (if not using Kafka)
  - entire directory (?)

docs/ (if empty)
  - old_*.md
```

**Consolidate**:
```
temp_scripts/ → tests/integration/
  - Keep only: comprehensive_tdd_test.py
  - Keep only: performance_test_100_urls.py
  - Move others to tests/
```

### 4.3 Remove Dead Code

Scan for:
- Unreachable code
- Unused functions
- Commented-out code blocks
- Empty files
- Unused imports

Tools:
```bash
# Find unused imports
pip install autoflake
autoflake --remove-all-unused-imports --recursive src/

# Find dead code
pip install vulture
vulture src/

# Format code
pip install black
black src/
```

### 4.4 Fix TODO/FIXME Comments

Search for all TODO/FIXME:
```bash
grep -r "TODO" src/
grep -r "FIXME" src/
grep -r "XXX" src/
grep -r "HACK" src/
```

Either:
- Fix the issue
- Create GitHub issue
- Remove if no longer relevant

### Deliverables
- All Priority 1-3 issues resolved
- All dead code removed
- All TODO/FIXME addressed
- Codebase reduced by 20-30%

---

## Phase 5: Testing & Documentation

**Duration**: 2 days
**Goal**: Comprehensive testing and documentation updates

### 5.1 Unit Tests

Create unit tests for all new utilities:

```
tests/unit/
  ├── __init__.py
  ├── test_delta_utils.py
  ├── test_redis_utils.py
  ├── test_metrics_utils.py
  ├── test_validation_utils.py
  ├── test_text_helpers.py
  └── test_url_helpers.py
```

Target: 80% code coverage

### 5.2 Integration Tests

Update integration tests:

```
tests/integration/
  ├── __init__.py
  ├── test_pipeline_e2e.py
  ├── test_stage1_integration.py
  ├── test_stage2_integration.py
  ├── test_stage3_integration.py
  └── test_stage4_integration.py
```

### 5.3 Update Documentation

**Update**:
- README.md (new structure)
- ARCHITECTURE.md (new organization)
- DEVELOPMENT.md (new helper usage)
- API_REFERENCE.md (new utilities)

**Create**:
- HELPERS_GUIDE.md (how to use global helpers)
- TESTING_GUIDE.md (how to run tests)
- TROUBLESHOOTING.md (common issues)

### 5.4 Final Validation

Run complete test suite:
```bash
# Unit tests
pytest tests/unit/ -v --cov=src

# Integration tests
pytest tests/integration/ -v

# Performance tests
python tests/performance/test_100_urls.py

# Linting
flake8 src/
pylint src/
mypy src/

# Type checking
mypy src/ --strict
```

### Deliverables
- Complete test suite (80%+ coverage)
- Updated documentation
- All tests passing
- Linting/type checking passing
- Performance benchmarks met

---

## Final Directory Structure

```
Scraping_project/
├── src/
│   ├── utils/               # NEW: Global utilities (4 files)
│   │   ├── __init__.py
│   │   ├── delta.py
│   │   ├── redis.py
│   │   ├── metrics.py
│   │   └── validation.py
│   ├── core/                # NEW: Core modules (3 files)
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── logging.py
│   │   └── exceptions.py
│   ├── helpers/             # NEW: Helper functions (3 files)
│   │   ├── __init__.py
│   │   ├── text.py
│   │   ├── url.py
│   │   └── time.py
│   ├── common/              # REFACTORED: Re-exports only (1 file)
│   │   └── __init__.py
│   ├── stage1/              # Unchanged (4 files)
│   ├── stage2/              # Refactored (2 files)
│   ├── stage3/              # Refactored (3 files)
│   ├── stage4/              # Refactored (3 files)
│   ├── lakehouse/           # Unchanged (2 files)
│   ├── orchestrator/        # Refactored (2 files)
│   └── analytics/           # Refactored (3 files)
├── tests/
│   ├── unit/                # NEW: Unit tests
│   ├── integration/         # MOVED: From temp_scripts
│   └── performance/         # NEW: Performance tests
├── dashboard/               # NEW: Custom dashboard
│   ├── index.html
│   ├── app.js
│   └── serve.py
├── config/
│   └── config.yml
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── monitoring/
│   └── (Grafana configs)
└── docs/
    ├── README.md
    ├── ARCHITECTURE.md
    ├── HELPERS_GUIDE.md
    └── TESTING_GUIDE.md
```

## File Count Summary

| Directory | Before | After | Status |
|-----------|--------|-------|--------|
| src/utils | 0 | 4 | ✅ NEW |
| src/core | 0 | 3 | ✅ NEW |
| src/helpers | 0 | 3 | ✅ NEW |
| src/common | 6 | 1 | ✅ FIXED |
| src/stage1 | 4 | 4 | ✅ OK |
| src/stage2 | 2 | 2 | ✅ OK |
| src/stage3 | 2 | 2 | ✅ OK |
| src/stage4 | 3 | 3 | ✅ OK |
| src/lakehouse | 2 | 2 | ✅ OK |
| src/orchestrator | 2 | 2 | ✅ OK |
| src/analytics | 3 | 3 | ✅ OK |
| **All directories** | **-** | **≤4** | **✅ GOAL MET** |

---

## Success Criteria

### Code Organization
- ✅ No directory has more than 4 files
- ✅ Clear separation of concerns
- ✅ Logical module hierarchy

### Code Quality
- ✅ All duplicate code eliminated
- ✅ Global helpers properly used
- ✅ All identified issues resolved
- ✅ Dead code removed
- ✅ 80%+ test coverage

### Documentation
- ✅ All modules documented
- ✅ Helper usage guide created
- ✅ Architecture updated
- ✅ API reference complete

### Testing
- ✅ Unit tests: 80%+ coverage
- ✅ Integration tests passing
- ✅ Performance tests passing
- ✅ Linting passing
- ✅ Type checking passing

---

## Estimated Timeline

| Phase | Duration | Parallel? |
|-------|----------|-----------|
| Phase 1: Audit | 1-2 days | No |
| Phase 2: Helpers | 2-3 days | Partial |
| Phase 3: Refactor | 3-4 days | No |
| Phase 4: Review | 2-3 days | Partial |
| Phase 5: Testing | 2 days | No |
| **Total** | **10-14 days** | - |

With 2-3 developers working in parallel on independent phases, this can be reduced to **7-10 days**.

---

## Next Steps

1. **Review this plan** with the team
2. **Create GitHub issues** for each phase
3. **Set up project board** with milestones
4. **Begin Phase 1** code audit
5. **Generate** CODE_AUDIT_REPORT.md
6. **Proceed** phase by phase

---

## Notes

- This plan is aggressive but achievable
- Each phase builds on the previous
- Testing is continuous throughout
- Documentation updates are incremental
- Git commits should be small and focused
- Each phase should be reviewed before proceeding

## Risk Mitigation

- **Risk**: Breaking existing functionality
  - **Mitigation**: Comprehensive tests before refactoring

- **Risk**: Import circular dependencies
  - **Mitigation**: Careful module design, lazy imports

- **Risk**: Timeline overrun
  - **Mitigation**: Prioritize critical changes, defer nice-to-haves

---

**Ready to begin Phase 1?**
