"""Enhanced test fixtures and utilities with efficiency metrics"""

import csv
import json
import random
import sys
import time
import psutil
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Tuple, Dict, Any

import pytest

# Add src to Python path for imports
REPO_ROOT = Path(__file__).parent.parent
SRC_DIR = REPO_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from common.urls import canonicalize_url_simple


# Performance tracking
class PerformanceTracker:
    """Track performance metrics across tests"""

    def __init__(self):
        self.metrics: Dict[str, Dict[str, Any]] = {}
        self.start_times: Dict[str, float] = {}

    def start_test(self, test_name: str):
        """Start tracking a test"""
        self.start_times[test_name] = time.perf_counter()
        start_memory = 0
        start_cpu = 0.0

        try:
            process = psutil.Process(os.getpid())
            start_memory = process.memory_info().rss
            start_cpu = process.cpu_percent()
        except (psutil.Error, PermissionError, SystemError):
            pass

        self.metrics[test_name] = {
            "start_time": self.start_times[test_name],
            "start_memory": start_memory,
            "start_cpu": start_cpu,
        }

    def end_test(self, test_name: str, operations: int = 1):
        """End tracking and calculate metrics"""
        if test_name not in self.start_times:
            return

        end_time = time.perf_counter()
        duration = end_time - self.start_times[test_name]

        metrics = self.metrics[test_name]
        end_memory = metrics.get("start_memory", 0)

        try:
            process = psutil.Process(os.getpid())
            end_memory = process.memory_info().rss
        except (psutil.Error, PermissionError, SystemError):
            pass
        metrics.update({
            "duration": duration,
            "operations": operations,
            "ops_per_second": operations / duration if duration > 0 else 0,
            "memory_delta": end_memory - metrics["start_memory"],
            "memory_delta_mb": (end_memory - metrics["start_memory"]) / (1024 * 1024)
        })

        return metrics


# Global performance tracker
performance_tracker = PerformanceTracker()


@pytest.fixture(scope="session")
def perf_tracker():
    """Provide performance tracker for tests"""
    return performance_tracker


@pytest.fixture(autouse=True)
def track_test_performance(request, perf_tracker):
    """Automatically track performance for all tests"""
    test_name = request.node.name
    perf_tracker.start_test(test_name)

    yield

    # Default to 1 operation unless test specifies otherwise
    operations = getattr(request.node, 'test_operations', 1)
    metrics = perf_tracker.end_test(test_name, operations)

    if metrics and hasattr(request.config, 'performance_data'):
        request.config.performance_data[test_name] = metrics


def _iter_seed_urls(limit: int = 1000) -> List[str]:
    """Load URLs from real seed CSVs when available."""
    candidates = [
        REPO_ROOT / "data" / "raw" / "uconn_urls.csv",
        Path(__file__).parent / "fixtures" / "sample_urls.csv",
    ]

    collected: List[str] = []
    for candidate in candidates:
        if not candidate.exists():
            continue

        with candidate.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            for row in reader:
                if not row:
                    continue
                url = row[0].strip()
                if not url:
                    continue
                collected.append(url)
                if len(collected) >= limit:
                    return collected

    return collected


def _random_seed_urls(count: int = 100, seed: int = 42) -> List[str]:
    """Load random URLs from uconn_urls.csv for truly random testing."""
    random.seed(seed)
    uconn_csv = REPO_ROOT / "data" / "raw" / "uconn_urls.csv"

    if not uconn_csv.exists():
        return []

    # First pass: count total URLs
    total_urls = 0
    with uconn_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if row and row[0].strip():
                total_urls += 1

    if total_urls == 0:
        return []

    # Select random line numbers
    selected_lines = sorted(random.sample(range(total_urls), min(count, total_urls)))

    # Second pass: extract selected URLs
    urls = []
    current_line = 0
    line_idx = 0

    with uconn_csv.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if row and row[0].strip():
                if line_idx < len(selected_lines) and current_line == selected_lines[line_idx]:
                    urls.append(row[0].strip())
                    line_idx += 1
                current_line += 1

    return urls


@pytest.fixture
def first_1000_urls() -> List[Tuple[str, str]]:
    """Return canonicalised URLs with hashes, prioritising real data."""
    urls = _iter_seed_urls(limit=1000)

    if len(urls) < 1000:
        base_paths = [
            "/about",
            "/admissions",
            "/academics",
            "/research",
            "/students",
        ]
        while len(urls) < 1000:
            idx = len(urls)
            urls.append(f"https://uconn.edu{base_paths[idx % len(base_paths)]}/{idx}")

    pairs: List[Tuple[str, str]] = []
    for url in urls[:1000]:
        canonical_url, url_hash = canonicalize_and_hash(url)
        pairs.append((canonical_url, url_hash))

    return pairs


@pytest.fixture
def sample_discovery_items(first_1000_urls):
    """Materialise DiscoveryItem objects based on canonical URLs."""
    from common.schemas import DiscoveryItem

    base_ts = datetime(2024, 1, 1, 0, 0, 0)
    items = []
    for idx, (url, url_hash) in enumerate(first_1000_urls):
        items.append(
            DiscoveryItem(
                source_url="https://uconn.edu/",
                discovered_url=url,
                first_seen=(base_ts + timedelta(seconds=idx)).isoformat(),
                url_hash=url_hash,
                discovery_depth=idx % 5,
            )
        )
    return items


@pytest.fixture
def sample_validation_results(first_1000_urls):
    """Create ValidationResult objects demonstrating mixed outcomes."""
    from common.schemas import ValidationResult

    results = []
    for idx, (url, url_hash) in enumerate(first_1000_urls):
        is_valid = idx % 7 != 0
        status_code = 200 if is_valid else 500
        content_type = "text/html; charset=utf-8" if is_valid else "application/octet-stream"

        results.append(
            ValidationResult(
                url=url,
                url_hash=url_hash,
                status_code=status_code,
                content_type=content_type,
                content_length=2048 + idx,
                response_time=0.05 * (idx % 4 + 1),
                is_valid=is_valid,
                error_message=None if is_valid else "Simulated server error",
                validated_at=datetime.utcnow().isoformat(),
            )
        )

    return results


@pytest.fixture
def temp_jsonl_file(tmp_path):
    """Create a temporary JSONL file for testing"""
    return tmp_path / "test_output.jsonl"


@pytest.fixture
def sample_config():
    """Sample configuration for testing"""
    return {
        'scrapy': {
            'concurrent_requests': 32,
            'download_delay': 0.1,
            'download_timeout': 10,
            'user_agent': 'Test-Spider/1.0'
        },
        'stages': {
            'discovery': {
                'max_depth': 3,
                'batch_size': 100,
                'output_file': 'data/processed/stage01/new_urls.jsonl'
            },
            'validation': {
                'max_workers': 10,
                'timeout': 5,
                'output_file': 'data/processed/stage02/validated_urls.jsonl'
            }
        }
    }


@pytest.fixture
def random_100_urls() -> List[Tuple[str, str]]:
    """Return a broad random sample of UConn URLs with canonical hashes."""
    urls = _random_seed_urls(count=1000, seed=42)

    pairs: List[Tuple[str, str]] = []
    for url in urls:
        canonical_url, url_hash = canonicalize_and_hash(url)
        pairs.append((canonical_url, url_hash))

    return pairs


@pytest.fixture
def random_discovery_items(random_100_urls):
    """Create DiscoveryItem objects from random URLs."""
    from common.schemas import DiscoveryItem

    base_ts = datetime(2024, 1, 1, 0, 0, 0)
    items = []
    for idx, (url, url_hash) in enumerate(random_100_urls):
        items.append(
            DiscoveryItem(
                source_url="https://uconn.edu/",
                discovered_url=url,
                first_seen=(base_ts + timedelta(seconds=idx)).isoformat(),
                url_hash=url_hash,
                discovery_depth=idx % 5,
            )
        )
    return items


@pytest.fixture
def load_test_urls() -> List[Tuple[str, str]]:
    """Return 1000 random URLs for load testing."""
    urls = _random_seed_urls(count=1000, seed=123)

    pairs: List[Tuple[str, str]] = []
    for url in urls:
        canonical_url, url_hash = canonicalize_and_hash(url)
        pairs.append((canonical_url, url_hash))

    return pairs


# Performance-aware fixtures
@pytest.fixture
def perf_random_urls():
    """Performance-tracked random URL fixture"""
    def _get_urls(count: int = 100):
        start_time = time.perf_counter()
        urls = _random_seed_urls(count=count, seed=42)

        pairs = []
        for url in urls:
            canonical_url, url_hash = canonicalize_and_hash(url)
            pairs.append((canonical_url, url_hash))

        duration = time.perf_counter() - start_time

        return {
            "urls": pairs,
            "metrics": {
                "count": len(pairs),
                "generation_time": duration,
                "urls_per_second": len(pairs) / duration if duration > 0 else 0
            }
        }

    return _get_urls


# Test session configuration
def pytest_configure(config):
    """Configure pytest with performance tracking"""
    config.performance_data = {}


def pytest_sessionfinish(session, exitstatus):
    """Generate performance report at end of test session"""
    if hasattr(session.config, 'performance_data'):
        performance_data = session.config.performance_data

        if performance_data:
            # Generate performance summary
            report_path = Path("test_performance_report.json")

            # Calculate summary metrics
            total_tests = len(performance_data)
            total_duration = sum(d.get("duration", 0) for d in performance_data.values())
            total_operations = sum(d.get("operations", 0) for d in performance_data.values())
            avg_ops_per_sec = sum(d.get("ops_per_second", 0) for d in performance_data.values()) / total_tests if total_tests > 0 else 0

            # Find performance insights
            fastest_test = min(performance_data.items(), key=lambda x: x[1].get("duration", float('inf')))
            slowest_test = max(performance_data.items(), key=lambda x: x[1].get("duration", 0))
            most_efficient = max(performance_data.items(), key=lambda x: x[1].get("ops_per_second", 0))

            summary_report = {
                "summary": {
                    "total_tests": total_tests,
                    "total_duration": total_duration,
                    "total_operations": total_operations,
                    "avg_ops_per_second": avg_ops_per_sec,
                    "fastest_test": {
                        "name": fastest_test[0],
                        "duration": fastest_test[1].get("duration", 0)
                    },
                    "slowest_test": {
                        "name": slowest_test[0],
                        "duration": slowest_test[1].get("duration", 0)
                    },
                    "most_efficient_test": {
                        "name": most_efficient[0],
                        "ops_per_second": most_efficient[1].get("ops_per_second", 0)
                    }
                },
                "detailed_metrics": performance_data
            }

            # Save report
            with open(report_path, 'w') as f:
                json.dump(summary_report, f, indent=2)

            print(f"\n📊 Performance report saved to: {report_path}")
            print(f"🎯 Test Summary: {total_tests} tests, {total_duration:.1f}s total, {avg_ops_per_sec:.0f} avg ops/s")
