#!/usr/bin/env python3
"""
Load and Stress Tests for Pipeline

Tests pipeline performance under various load conditions:
- High volume URL processing
- Concurrent stage execution
- Memory usage under load
- Delta Lake performance
- Redis performance
- System resource limits

Performance Benchmarks:
- Stage 1: 100+ URLs/min
- Stage 2: 10+ pages/sec
- Stage 3: 2+ summaries/sec
- Memory: < 2GB per worker
"""

import pytest
import asyncio
import time
import psutil
import sys
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed

project_root = Path(__file__).parent.parent.parent / "Scraping_project"
sys.path.insert(0, str(project_root))

from src.lakehouse.lakehouse_manager import get_delta_manager


@dataclass
class LoadTestResults:
    """Results from a load test."""
    test_name: str
    duration: float
    items_processed: int
    throughput: float
    peak_memory_mb: float
    success_rate: float
    errors: List[str]

    def __str__(self):
        return f"""
{self.test_name}:
  Duration: {self.duration:.2f}s
  Items Processed: {self.items_processed}
  Throughput: {self.throughput:.2f} items/sec
  Peak Memory: {self.peak_memory_mb:.2f} MB
  Success Rate: {self.success_rate:.1%}
  Errors: {len(self.errors)}
"""


class PerformanceMonitor:
    """Monitor system performance during tests."""

    def __init__(self):
        self.process = psutil.Process()
        self.start_time = None
        self.peak_memory = 0
        self.measurements = []

    def start(self):
        """Start monitoring."""
        self.start_time = time.time()
        self.peak_memory = 0
        self.measurements = []

    def measure(self):
        """Take a performance measurement."""
        memory_info = self.process.memory_info()
        memory_mb = memory_info.rss / 1024 / 1024
        self.peak_memory = max(self.peak_memory, memory_mb)

        self.measurements.append({
            'timestamp': time.time() - self.start_time,
            'memory_mb': memory_mb,
            'cpu_percent': self.process.cpu_percent()
        })

    def get_duration(self) -> float:
        """Get elapsed time."""
        return time.time() - self.start_time


class TestLoadStress:
    """Load and stress tests for pipeline."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """Setup and teardown for each test."""
        self.delta = get_delta_manager()
        self.monitor = PerformanceMonitor()
        yield
        self.delta = None
        self.monitor = None

    def test_01_high_volume_url_seeding(self):
        """Test seeding 10,000 URLs."""
        print("\n" + "=" * 80)
        print("TEST: High Volume URL Seeding (10,000 URLs)")
        print("=" * 80)

        url_count = 10000
        batch_size = 1000
        errors = []

        self.monitor.start()

        urls = [
            {"url": f"https://uconn.edu/page{i}", "priority": i % 5}
            for i in range(url_count)
        ]

        try:
            for i in range(0, url_count, batch_size):
                batch = urls[i:i+batch_size]
                try:
                    self.delta.write("seed_urls", batch, mode="append")
                    self.monitor.measure()
                except Exception as e:
                    errors.append(f"Batch {i//batch_size}: {str(e)}")

            duration = self.monitor.get_duration()
            success_rate = 1 - (len(errors) / (url_count / batch_size))

            results = LoadTestResults(
                test_name="High Volume URL Seeding",
                duration=duration,
                items_processed=url_count,
                throughput=url_count / duration,
                peak_memory_mb=self.monitor.peak_memory,
                success_rate=success_rate,
                errors=errors
            )

            print(results)

            assert success_rate >= 0.95, f"Success rate {success_rate:.1%} below 95%"
            assert results.throughput >= 1000, f"Throughput {results.throughput:.0f} below 1000 URLs/sec"

            print(f"✅ PASS: {url_count} URLs seeded in {duration:.2f}s ({results.throughput:.0f} URLs/sec)")

        except Exception as e:
            pytest.fail(f"High volume seeding failed: {e}")

    def test_02_concurrent_stage2_processing(self):
        """Test concurrent Stage 2 processing with 100 URLs."""
        print("\n" + "=" * 80)
        print("TEST: Concurrent Stage 2 Processing (100 URLs)")
        print("=" * 80)

        url_count = 100
        errors = []

        test_data = [
            {
                "url": f"https://uconn.edu/concurrent{i}",
                "word_count": 500 + i,
                "text_length": 3000,
                "html_length": 5000,
                "text_html_ratio": 0.6,
                "has_quality_content": True,
                "is_massive_doc": False
            }
            for i in range(url_count)
        ]

        self.monitor.start()

        def process_batch(batch_id, data_batch):
            try:
                self.delta.write("stage2_page_analysis", data_batch, mode="append")
                return batch_id, True, None
            except Exception as e:
                return batch_id, False, str(e)

        batch_size = 10
        batches = [test_data[i:i+batch_size] for i in range(0, url_count, batch_size)]

        try:
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [
                    executor.submit(process_batch, i, batch)
                    for i, batch in enumerate(batches)
                ]

                for future in as_completed(futures):
                    batch_id, success, error = future.result()
                    if not success:
                        errors.append(f"Batch {batch_id}: {error}")
                    self.monitor.measure()

            duration = self.monitor.get_duration()
            success_rate = 1 - (len(errors) / len(batches))

            results = LoadTestResults(
                test_name="Concurrent Stage 2 Processing",
                duration=duration,
                items_processed=url_count,
                throughput=url_count / duration,
                peak_memory_mb=self.monitor.peak_memory,
                success_rate=success_rate,
                errors=errors
            )

            print(results)

            assert success_rate >= 0.90, f"Success rate {success_rate:.1%} below 90%"
            assert results.throughput >= 5, f"Throughput {results.throughput:.2f} below 5 pages/sec"

            print(f"✅ PASS: {url_count} pages processed in {duration:.2f}s ({results.throughput:.2f} pages/sec)")

        except Exception as e:
            pytest.fail(f"Concurrent processing failed: {e}")

    def test_03_memory_usage_under_load(self):
        """Test memory usage with 1000 large documents."""
        print("\n" + "=" * 80)
        print("TEST: Memory Usage Under Load (1000 documents)")
        print("=" * 80)

        doc_count = 1000
        large_text = "Large document content " * 500

        self.monitor.start()

        try:
            for i in range(0, doc_count, 100):
                batch = [
                    {
                        "url": f"https://uconn.edu/large{j}",
                        "title": f"Large Document {j}",
                        "text": large_text,
                        "word_count": 10000
                    }
                    for j in range(i, i + 100)
                ]

                self.delta.write("stage2_page_analysis", batch, mode="append")
                self.monitor.measure()

            duration = self.monitor.get_duration()
            peak_memory = self.monitor.peak_memory

            print(f"\n📊 Memory Usage:")
            print(f"   Peak Memory: {peak_memory:.2f} MB")
            print(f"   Duration: {duration:.2f}s")
            print(f"   Documents: {doc_count}")

            assert peak_memory < 2048, f"Peak memory {peak_memory:.0f}MB exceeds 2GB limit"

            print(f"✅ PASS: Memory usage stayed within limits ({peak_memory:.2f} MB < 2048 MB)")

        except Exception as e:
            pytest.fail(f"Memory test failed: {e}")

    def test_04_delta_lake_read_performance(self):
        """Test Delta Lake read performance with 5000 records."""
        print("\n" + "=" * 80)
        print("TEST: Delta Lake Read Performance (5000 records)")
        print("=" * 80)

        record_count = 5000

        test_data = [
            {"url": f"https://uconn.edu/read{i}", "value": i}
            for i in range(record_count)
        ]

        try:
            self.delta.write("stage1_discovery", test_data, mode="overwrite")
            print(f"📝 Wrote {record_count} records")

            self.monitor.start()

            for _ in range(10):
                data = self.delta.read("stage1_discovery")
                assert len(data) >= record_count
                self.monitor.measure()

            duration = self.monitor.get_duration()
            reads_per_sec = 10 / duration

            print(f"\n📊 Read Performance:")
            print(f"   Reads: 10")
            print(f"   Duration: {duration:.2f}s")
            print(f"   Throughput: {reads_per_sec:.2f} reads/sec")

            assert reads_per_sec >= 1, f"Read performance {reads_per_sec:.2f} below 1 read/sec"

            print(f"✅ PASS: Read performance acceptable ({reads_per_sec:.2f} reads/sec)")

        except Exception as e:
            pytest.fail(f"Read performance test failed: {e}")

    def test_05_delta_lake_write_performance(self):
        """Test Delta Lake write performance with 1000 batches."""
        print("\n" + "=" * 80)
        print("TEST: Delta Lake Write Performance (1000 batches)")
        print("=" * 80)

        batch_count = 1000
        batch_size = 10
        errors = []

        self.monitor.start()

        try:
            for i in range(batch_count):
                batch = [
                    {"url": f"https://uconn.edu/write{i}_{j}", "batch": i}
                    for j in range(batch_size)
                ]

                try:
                    self.delta.write("stage1_discovery", batch, mode="append")
                    if i % 100 == 0:
                        self.monitor.measure()
                except Exception as e:
                    errors.append(f"Batch {i}: {str(e)}")

            duration = self.monitor.get_duration()
            success_rate = 1 - (len(errors) / batch_count)
            throughput = (batch_count * batch_size) / duration

            print(f"\n📊 Write Performance:")
            print(f"   Batches: {batch_count}")
            print(f"   Records: {batch_count * batch_size}")
            print(f"   Duration: {duration:.2f}s")
            print(f"   Throughput: {throughput:.0f} records/sec")
            print(f"   Success Rate: {success_rate:.1%}")
            print(f"   Errors: {len(errors)}")

            assert success_rate >= 0.95, f"Success rate {success_rate:.1%} below 95%"
            assert throughput >= 100, f"Throughput {throughput:.0f} below 100 records/sec"

            print(f"✅ PASS: Write performance acceptable ({throughput:.0f} records/sec)")

        except Exception as e:
            pytest.fail(f"Write performance test failed: {e}")

    def test_06_redis_performance(self):
        """Test Redis performance with 10,000 operations."""
        print("\n" + "=" * 80)
        print("TEST: Redis Performance (10,000 operations)")
        print("=" * 80)

        try:
            import redis
            client = redis.Redis(host='localhost', port=6379, decode_responses=True)
            client.ping()
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")

        op_count = 10000
        errors = []

        self.monitor.start()

        try:
            for i in range(op_count):
                try:
                    key = f"test:load:{i}"
                    client.set(key, f"value{i}")

                    if i % 100 == 0:
                        value = client.get(key)
                        assert value == f"value{i}"

                    if i % 1000 == 0:
                        self.monitor.measure()
                except Exception as e:
                    errors.append(f"Op {i}: {str(e)}")

            client.delete(*[f"test:load:{i}" for i in range(op_count)])

            duration = self.monitor.get_duration()
            success_rate = 1 - (len(errors) / op_count)
            throughput = op_count / duration

            print(f"\n📊 Redis Performance:")
            print(f"   Operations: {op_count}")
            print(f"   Duration: {duration:.2f}s")
            print(f"   Throughput: {throughput:.0f} ops/sec")
            print(f"   Success Rate: {success_rate:.1%}")
            print(f"   Errors: {len(errors)}")

            assert success_rate >= 0.99, f"Success rate {success_rate:.1%} below 99%"
            assert throughput >= 1000, f"Throughput {throughput:.0f} below 1000 ops/sec"

            print(f"✅ PASS: Redis performance acceptable ({throughput:.0f} ops/sec)")

        except Exception as e:
            pytest.fail(f"Redis performance test failed: {e}")

    def test_07_sustained_load(self):
        """Test sustained load for 60 seconds."""
        print("\n" + "=" * 80)
        print("TEST: Sustained Load (60 seconds)")
        print("=" * 80)

        duration_seconds = 60
        batch_size = 100
        errors = []
        total_processed = 0

        self.monitor.start()

        try:
            end_time = time.time() + duration_seconds

            while time.time() < end_time:
                batch = [
                    {
                        "url": f"https://uconn.edu/sustained{total_processed + i}",
                        "value": total_processed + i
                    }
                    for i in range(batch_size)
                ]

                try:
                    self.delta.write("stage1_discovery", batch, mode="append")
                    total_processed += batch_size
                    self.monitor.measure()
                except Exception as e:
                    errors.append(str(e))

            actual_duration = self.monitor.get_duration()
            throughput = total_processed / actual_duration

            print(f"\n📊 Sustained Load Results:")
            print(f"   Duration: {actual_duration:.2f}s")
            print(f"   Total Processed: {total_processed}")
            print(f"   Avg Throughput: {throughput:.2f} records/sec")
            print(f"   Peak Memory: {self.monitor.peak_memory:.2f} MB")
            print(f"   Errors: {len(errors)}")

            success_rate = 1 - (len(errors) / (total_processed / batch_size))

            assert success_rate >= 0.95, f"Success rate {success_rate:.1%} below 95%"
            assert throughput >= 50, f"Sustained throughput {throughput:.2f} below 50 records/sec"

            print(f"✅ PASS: System sustained load successfully")

        except Exception as e:
            pytest.fail(f"Sustained load test failed: {e}")

    def test_08_burst_traffic(self):
        """Test handling burst traffic (1000 concurrent requests)."""
        print("\n" + "=" * 80)
        print("TEST: Burst Traffic (1000 concurrent requests)")
        print("=" * 80)

        request_count = 1000
        errors = []

        def write_single_record(record_id):
            try:
                data = [{
                    "url": f"https://uconn.edu/burst{record_id}",
                    "value": record_id
                }]
                self.delta.write("stage1_discovery", data, mode="append")
                return record_id, True, None
            except Exception as e:
                return record_id, False, str(e)

        self.monitor.start()

        try:
            with ThreadPoolExecutor(max_workers=50) as executor:
                futures = [
                    executor.submit(write_single_record, i)
                    for i in range(request_count)
                ]

                for future in as_completed(futures):
                    record_id, success, error = future.result()
                    if not success:
                        errors.append(f"Record {record_id}: {error}")

                    if len(errors) + len([f for f in futures if f.done()]) % 100 == 0:
                        self.monitor.measure()

            duration = self.monitor.get_duration()
            success_rate = 1 - (len(errors) / request_count)
            throughput = request_count / duration

            print(f"\n📊 Burst Traffic Results:")
            print(f"   Requests: {request_count}")
            print(f"   Duration: {duration:.2f}s")
            print(f"   Throughput: {throughput:.0f} requests/sec")
            print(f"   Success Rate: {success_rate:.1%}")
            print(f"   Peak Memory: {self.monitor.peak_memory:.2f} MB")
            print(f"   Errors: {len(errors)}")

            assert success_rate >= 0.80, f"Success rate {success_rate:.1%} below 80% for burst"

            print(f"✅ PASS: System handled burst traffic (success rate: {success_rate:.1%})")

        except Exception as e:
            pytest.fail(f"Burst traffic test failed: {e}")


def run_load_tests():
    """Run all load and stress tests."""
    print("\n" + "=" * 80)
    print("🏋️  LOAD AND STRESS TESTS")
    print("=" * 80)
    print()
    print("These tests evaluate pipeline performance under heavy load:")
    print("  - High volume URL processing")
    print("  - Concurrent operations")
    print("  - Memory usage limits")
    print("  - Sustained load handling")
    print("  - Burst traffic management")
    print()
    print("⚠️  WARNING: These tests will generate significant load")
    print("=" * 80)
    print()

    pytest_args = [
        __file__,
        "-v",
        "--tb=short",
        "-s",
        "--timeout=300"
    ]

    exit_code = pytest.main(pytest_args)

    print()
    print("=" * 80)
    if exit_code == 0:
        print("✅ ALL LOAD TESTS PASSED")
    else:
        print("❌ SOME LOAD TESTS FAILED")
    print("=" * 80)

    return exit_code


if __name__ == "__main__":
    exit(run_load_tests())
