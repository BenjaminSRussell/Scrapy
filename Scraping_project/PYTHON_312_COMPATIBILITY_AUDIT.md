# Python 3.12 Compatibility Audit Report

## 🚨 Critical Issues Found

### 1. **Deprecated `asyncio.get_event_loop()` Usage**
**Location**: `src/orchestrator/main.py:81`
```python
loop = asyncio.get_event_loop()  # DEPRECATED in Python 3.12
try:
    await loop.run_in_executor(None, process.start)
```

**Issue**: `asyncio.get_event_loop()` is deprecated in Python 3.12 and will raise `DeprecationWarning`. In future versions, it may be removed entirely.

**Fix Required**: Replace with:
```python
loop = asyncio.get_running_loop()  # or use asyncio.to_thread() for Python 3.9+
try:
    await loop.run_in_executor(None, process.start)
```

### 2. **Multiple `asyncio.get_event_loop()` in Data Refresh Module**
**Locations**:
- `src/orchestrator/data_refresh.py:` (multiple instances)
- Used for timing: `asyncio.get_event_loop().time()`

**Issue**: Same deprecated pattern repeated throughout the codebase.

**Fix Required**: Replace with `time.perf_counter()` or `asyncio.get_running_loop().time()`

## 🔧 Dependency Compatibility Issues

### 1. **Twisted Version Constraints**
**Current**: `Twisted>=21.7.0,<23.0.0`
**Issue**: Python 3.12 requires Twisted 23.8.0+ for full compatibility

**Fix Required**: Update to:
```
Twisted>=23.8.0,<25.0.0
```

### 2. **NumPy Version Constraints**
**Current**: `numpy>=1.20.0,<2.0.0`
**Issue**: Python 3.12 needs NumPy 1.26.0+ for optimal performance and compatibility

**Fix Required**: Update to:
```
numpy>=1.26.0,<2.0.0
```

### 3. **aiohttp Version**
**Current**: `aiohttp>=3.7.0,<4.0.0`
**Issue**: Python 3.12 benefits from aiohttp 3.9.0+ which includes performance improvements

**Fix Required**: Update to:
```
aiohttp>=3.9.0,<4.0.0
```

### 4. **Scrapy Version**
**Current**: `scrapy>=2.5.0,<2.12.0`
**Issue**: Python 3.12 requires Scrapy 2.11.0+ for asyncio compatibility fixes

**Fix Required**: Update to:
```
scrapy>=2.11.0,<3.0.0
```

## ⚠️ Type Hints Issues

### 1. **Legacy `typing` Module Usage**
**Issue**: Using `from typing import List, Dict, Optional` throughout codebase

**Python 3.12 Improvement**: Can use built-in generics:
```python
# Old (still works but verbose)
from typing import List, Dict, Optional

# New (Python 3.9+, recommended for 3.12)
list[str]  # instead of List[str]
dict[str, int]  # instead of Dict[str, int]
str | None  # instead of Optional[str]
```

**Status**: Not critical, but recommended for modernization

### 2. **typing-extensions Dependency**
**Current**: `typing-extensions>=3.10.0,<5.0.0`
**Issue**: With Python 3.12, most features are in stdlib

**Fix**: Can be made optional:
```
typing-extensions>=4.7.0; python_version<"3.12"
```

## 🔄 Asyncio Pattern Issues

### 1. **Mixed Twisted/asyncio Event Loops**
**Location**: `src/orchestrator/pipeline.py:300`
```python
from twisted.internet import asyncioreactor
```

**Issue**: Python 3.12 has stricter event loop policies that may conflict with Twisted's asyncio reactor.

**Risk**: Potential runtime errors when mixing event loops

**Mitigation**: Use subprocess approach for Scrapy execution (already implemented as fallback)

### 2. **Queue Creation in Sync Context**
**Location**: `src/orchestrator/pipeline.py:56`
**Issue**: Creating asyncio.Queue outside event loop context may warn in Python 3.12

**Fix**: Already handled with lazy queue creation pattern ✅

## 📦 Build and Installation Issues

### 1. **C Extension Dependencies**
**Affected packages**: `lxml`, `numpy`, `psutil`
**Issue**: May need wheel updates for Python 3.12 or compilation from source

**Mitigation**: Pin to versions with pre-built wheels:
```
lxml>=5.0.0  # Has Python 3.12 wheels
psutil>=5.9.5  # Has Python 3.12 wheels
```

### 2. **Distutils Deprecation**
**Issue**: Python 3.12 removed `distutils` completely
**Status**: ✅ No direct usage found in codebase, but dependencies may be affected

## 🧪 Test Framework Compatibility

### 1. **pytest-asyncio Version**
**Current**: `pytest-asyncio>=0.18.0,<1.0.0`
**Issue**: Python 3.12 needs pytest-asyncio 0.21.0+ for proper async support

**Fix Required**: Update to:
```
pytest-asyncio>=0.21.0,<1.0.0
```

## 📋 Action Plan for Python 3.12 Upgrade

### Phase 1: Critical Fixes (Required)
1. **Replace all `asyncio.get_event_loop()` calls**
2. **Update Twisted to 23.8.0+**
3. **Update pytest-asyncio to 0.21.0+**
4. **Test async/await patterns with new event loop policies**

### Phase 2: Dependency Updates (Recommended)
1. **Update NumPy to 1.26.0+**
2. **Update aiohttp to 3.9.0+**
3. **Update Scrapy to 2.11.0+**
4. **Verify all C extensions have Python 3.12 wheels**

### Phase 3: Modernization (Optional)
1. **Migrate to built-in generic types**
2. **Make typing-extensions conditional**
3. **Add Python 3.12 to CI/testing matrix**

## 🔍 Testing Recommendations

### 1. **Create Python 3.12 Test Environment**
```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
pip install -e .
```

### 2. **Run Compatibility Tests**
```bash
python run_tests.py smoke  # Quick validation
python run_tests.py all    # Full test suite
```

### 3. **Monitor Deprecation Warnings**
```bash
python -W error::DeprecationWarning main.py --stage 1
```

## 💡 Summary

**Upgrade Difficulty**: 🟡 **Moderate**

**Critical Blockers**: 2 (asyncio.get_event_loop usage, Twisted version)
**Dependency Updates**: 4 packages need version bumps
**Code Changes**: Minimal (mostly search-and-replace)

**Estimated Effort**: 1-2 days for critical fixes, 1 week for full modernization

The codebase is generally well-positioned for Python 3.12, but the deprecated asyncio patterns must be addressed before upgrading.