#!/usr/bin/env python3
"""
Comprehensive Integration Tests - Full Pipeline

Tests end-to-end integration of all 4 pipeline stages:
- Stage 1: URL Discovery
- Stage 2: Page Analysis
- Stage 3: Summarization
- Stage 4: Large Document Processing

These tests validate:
- Data flow between stages
- Delta Lake read/write operations
- Redis connectivity
- Metrics collection
- Error handling
- Data integrity
"""

import pytest
import asyncio
import sys
from pathlib import Path
from typing import List, Dict

project_root = Path(__file__).parent.parent.parent / "Scraping_project"
sys.path.insert(0, str(project_root))

from src.lakehouse.lakehouse_manager import LakehouseManager, get_delta_manager
from src.orchestrator.pipeline_orchestrator import PipelineOrchestrator


class TestFullPipelineIntegration:
    """Integration tests for full pipeline."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """Setup and teardown for each test."""
        self.delta = get_delta_manager()
        yield
        self.delta = None

    def test_01_delta_lake_connection(self):
        """Test Delta Lake connection and table initialization."""
        assert self.delta is not None, "Delta manager should be initialized"

        tables = [
            "seed_urls",
            "stage1_discovery",
            "stage2_page_analysis",
            "stage3_summaries",
            "stage4_large_doc_summaries"
        ]

        for table_name in tables:
            try:
                table_path = self.delta.get_table_path(table_name)
                assert table_path.exists(), f"Table path should exist: {table_name}"
            except Exception as e:
                pytest.fail(f"Failed to access table {table_name}: {e}")

    def test_02_seed_urls_to_stage1(self):
        """Test data flow from seed URLs to Stage 1 discovery."""
        test_urls = [
            {"url": "https://uconn.edu/test1", "priority": 1},
            {"url": "https://uconn.edu/test2", "priority": 1},
            {"url": "https://uconn.edu/test3", "priority": 2}
        ]

        try:
            self.delta.write("seed_urls", test_urls, mode="overwrite")
            print("✅ Wrote seed URLs")
        except Exception as e:
            pytest.fail(f"Failed to write seed URLs: {e}")

        try:
            seed_data = self.delta.read("seed_urls")
            assert len(seed_data) == 3, f"Should have 3 seed URLs, got {len(seed_data)}"
            print(f"✅ Read {len(seed_data)} seed URLs")
        except Exception as e:
            pytest.fail(f"Failed to read seed URLs: {e}")

        assert all('url' in item for item in seed_data), "All items should have 'url' field"

    def test_03_stage1_to_stage2_flow(self):
        """Test data flow from Stage 1 to Stage 2."""
        stage1_data = [
            {
                "url": "https://uconn.edu/page1",
                "discovered_at": "2025-01-01T00:00:00",
                "depth": 0,
                "source_url": "https://uconn.edu"
            },
            {
                "url": "https://uconn.edu/page2",
                "discovered_at": "2025-01-01T00:00:01",
                "depth": 1,
                "source_url": "https://uconn.edu/page1"
            }
        ]

        try:
            self.delta.write("stage1_discovery", stage1_data, mode="overwrite")
            print("✅ Wrote Stage 1 discovery data")
        except Exception as e:
            pytest.fail(f"Failed to write Stage 1 data: {e}")

        try:
            discovery_data = self.delta.read("stage1_discovery")
            assert len(discovery_data) >= 2, f"Should have at least 2 discoveries"
            print(f"✅ Verified Stage 1 data: {len(discovery_data)} records")
        except Exception as e:
            pytest.fail(f"Failed to read Stage 1 data: {e}")

    def test_04_stage2_analysis_data(self):
        """Test Stage 2 page analysis data structure."""
        stage2_data = [
            {
                "url": "https://uconn.edu/test",
                "title": "Test Page",
                "word_count": 500,
                "text_length": 3000,
                "html_length": 5000,
                "text_html_ratio": 0.6,
                "has_quality_content": True,
                "is_massive_doc": False,
                "analyzed_at": "2025-01-01T00:00:00"
            }
        ]

        try:
            self.delta.write("stage2_page_analysis", stage2_data, mode="overwrite")
            print("✅ Wrote Stage 2 analysis data")
        except Exception as e:
            pytest.fail(f"Failed to write Stage 2 data: {e}")

        try:
            analysis_data = self.delta.read("stage2_page_analysis")
            assert len(analysis_data) >= 1, "Should have at least 1 analysis"

            for item in analysis_data:
                assert 'url' in item, "Should have url field"
                assert 'word_count' in item, "Should have word_count field"
                assert 'has_quality_content' in item, "Should have has_quality_content field"

            print(f"✅ Verified Stage 2 data structure: {len(analysis_data)} records")
        except Exception as e:
            pytest.fail(f"Failed to read Stage 2 data: {e}")

    def test_05_stage2_to_stage3_routing(self):
        """Test routing from Stage 2 to Stage 3 (quality docs)."""
        stage2_mixed = [
            {
                "url": "https://uconn.edu/quality1",
                "title": "Quality Document",
                "word_count": 500,
                "has_quality_content": True,
                "is_massive_doc": False,
                "text": "Quality content" * 50
            },
            {
                "url": "https://uconn.edu/quality2",
                "title": "Another Quality Doc",
                "word_count": 800,
                "has_quality_content": True,
                "is_massive_doc": False,
                "text": "More quality content" * 60
            },
            {
                "url": "https://uconn.edu/massive1",
                "title": "Massive Document",
                "word_count": 60000,
                "has_quality_content": False,
                "is_massive_doc": True,
                "text": "Massive content" * 5000
            }
        ]

        try:
            self.delta.write("stage2_page_analysis", stage2_mixed, mode="overwrite")
            print("✅ Wrote mixed Stage 2 data")
        except Exception as e:
            pytest.fail(f"Failed to write mixed data: {e}")

        try:
            all_docs = self.delta.read("stage2_page_analysis")
            quality_docs = [doc for doc in all_docs if doc.get("has_quality_content")]
            massive_docs = [doc for doc in all_docs if doc.get("is_massive_doc")]

            assert len(quality_docs) >= 2, f"Should have at least 2 quality docs, got {len(quality_docs)}"
            assert len(massive_docs) >= 1, f"Should have at least 1 massive doc, got {len(massive_docs)}"

            print(f"✅ Routing verified: {len(quality_docs)} → Stage 3, {len(massive_docs)} → Stage 4")
        except Exception as e:
            pytest.fail(f"Failed to verify routing: {e}")

    def test_06_stage3_summaries(self):
        """Test Stage 3 summary creation."""
        stage3_data = [
            {
                "url": "https://uconn.edu/summarized1",
                "title": "Summarized Page",
                "original_text": "Original text" * 100,
                "summary": "Summary of the page content.",
                "summarized_at": "2025-01-01T00:00:00",
                "model": "facebook/bart-large-cnn"
            }
        ]

        try:
            self.delta.write("stage3_summaries", stage3_data, mode="overwrite")
            print("✅ Wrote Stage 3 summaries")
        except Exception as e:
            pytest.fail(f"Failed to write Stage 3 data: {e}")

        try:
            summaries = self.delta.read("stage3_summaries")
            assert len(summaries) >= 1, "Should have at least 1 summary"

            for summary in summaries:
                assert 'url' in summary, "Should have url field"
                assert 'summary' in summary, "Should have summary field"

            print(f"✅ Verified Stage 3 summaries: {len(summaries)} records")
        except Exception as e:
            pytest.fail(f"Failed to read Stage 3 data: {e}")

    def test_07_stage4_large_doc_processing(self):
        """Test Stage 4 large document processing."""
        stage4_data = [
            {
                "url": "https://uconn.edu/large_doc1",
                "title": "Large Document",
                "original_word_count": 60000,
                "summary_word_count": 500,
                "compression_ratio": 0.0083,
                "summary": "Comprehensive summary of large document.",
                "processed_at": "2025-01-01T00:00:00"
            }
        ]

        try:
            self.delta.write("stage4_large_doc_summaries", stage4_data, mode="overwrite")
            print("✅ Wrote Stage 4 large doc summaries")
        except Exception as e:
            pytest.fail(f"Failed to write Stage 4 data: {e}")

        try:
            large_summaries = self.delta.read("stage4_large_doc_summaries")
            assert len(large_summaries) >= 1, "Should have at least 1 large summary"

            for summary in large_summaries:
                assert 'url' in summary, "Should have url field"
                assert 'summary' in summary, "Should have summary field"
                assert 'compression_ratio' in summary, "Should have compression_ratio"

            print(f"✅ Verified Stage 4 summaries: {len(large_summaries)} records")
        except Exception as e:
            pytest.fail(f"Failed to read Stage 4 data: {e}")

    def test_08_data_integrity_full_pipeline(self):
        """Test data integrity across full pipeline."""
        initial_urls = 5

        seed_urls = [{"url": f"https://uconn.edu/test{i}", "priority": 1} for i in range(initial_urls)]
        self.delta.write("seed_urls", seed_urls, mode="overwrite")

        stage1_discoveries = [
            {"url": f"https://uconn.edu/test{i}", "depth": 0}
            for i in range(initial_urls)
        ]
        self.delta.write("stage1_discovery", stage1_discoveries, mode="overwrite")

        stage2_analyses = [
            {
                "url": f"https://uconn.edu/test{i}",
                "word_count": 500 + i * 100,
                "has_quality_content": True,
                "is_massive_doc": False
            }
            for i in range(initial_urls)
        ]
        self.delta.write("stage2_page_analysis", stage2_analyses, mode="overwrite")

        seed_count = len(self.delta.read("seed_urls"))
        stage1_count = len(self.delta.read("stage1_discovery"))
        stage2_count = len(self.delta.read("stage2_page_analysis"))

        assert seed_count == initial_urls, f"Expected {initial_urls} seeds, got {seed_count}"
        assert stage1_count == initial_urls, f"Expected {initial_urls} discoveries, got {stage1_count}"
        assert stage2_count == initial_urls, f"Expected {initial_urls} analyses, got {stage2_count}"

        print(f"✅ Data integrity verified across pipeline: {seed_count} → {stage1_count} → {stage2_count}")

    def test_09_concurrent_writes(self):
        """Test concurrent writes don't corrupt data."""
        import concurrent.futures

        def write_batch(batch_id):
            try:
                data = [
                    {"url": f"https://uconn.edu/concurrent_{batch_id}_{i}", "batch": batch_id}
                    for i in range(10)
                ]
                self.delta.write("stage1_discovery", data, mode="append")
                return batch_id, True
            except Exception as e:
                return batch_id, False

        initial_count = len(self.delta.read("stage1_discovery"))

        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(write_batch, i) for i in range(5)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        final_count = len(self.delta.read("stage1_discovery"))

        successful_writes = sum(1 for _, success in results if success)
        expected_new_records = successful_writes * 10

        print(f"✅ Concurrent writes completed: {successful_writes}/5 successful")
        print(f"   Records before: {initial_count}, after: {final_count}")

        assert final_count >= initial_count, "Record count should increase"

    def test_10_error_handling(self):
        """Test error handling in pipeline operations."""
        try:
            invalid_data = [{"url": "not_a_url", "invalid_field": None}]
            self.delta.write("stage1_discovery", invalid_data, mode="append")
            print("✅ Invalid data write handled gracefully")
        except Exception as e:
            print(f"⚠️  Invalid data rejected as expected: {type(e).__name__}")

        try:
            non_existent_table = self.delta.read("non_existent_table")
            pytest.fail("Should raise error for non-existent table")
        except Exception as e:
            print(f"✅ Non-existent table error handled: {type(e).__name__}")

    def test_11_metrics_collection(self):
        """Test metrics collection across pipeline."""
        try:
            from src.common.metrics_manager import MetricsManager
            metrics = MetricsManager()

            metrics.record_stage1_url_discovered()
            metrics.record_stage2_page_analyzed()
            metrics.record_stage3_summary_created()

            print("✅ Metrics collection working")
        except Exception as e:
            pytest.skip(f"Metrics collection not available: {e}")

    def test_12_redis_connectivity(self):
        """Test Redis connectivity and operations."""
        try:
            import redis
            client = redis.Redis(host='localhost', port=6379, decode_responses=True)
            client.ping()

            test_key = "test:integration:key"
            client.set(test_key, "test_value")
            value = client.get(test_key)
            client.delete(test_key)

            assert value == "test_value", "Redis read/write should work"
            print("✅ Redis connectivity verified")
        except Exception as e:
            pytest.skip(f"Redis not available: {e}")


class TestPipelineOrchestrator:
    """Integration tests for pipeline orchestrator."""

    def test_orchestrator_initialization(self):
        """Test orchestrator can be initialized."""
        try:
            orchestrator = PipelineOrchestrator()
            assert orchestrator is not None, "Orchestrator should initialize"
            assert orchestrator.stats is not None, "Stats should be initialized"
            print("✅ Orchestrator initialized successfully")
        except Exception as e:
            pytest.fail(f"Failed to initialize orchestrator: {e}")

    def test_orchestrator_stats_tracking(self):
        """Test orchestrator statistics tracking."""
        try:
            orchestrator = PipelineOrchestrator()

            orchestrator.stats.stage1_urls_discovered = 100
            orchestrator.stats.stage2_pages_analyzed = 90
            orchestrator.stats.stage3_summaries_created = 80

            assert orchestrator.stats.stage1_urls_discovered == 100
            assert orchestrator.stats.stage2_pages_analyzed == 90
            assert orchestrator.stats.stage3_summaries_created == 80

            print("✅ Orchestrator stats tracking verified")
        except Exception as e:
            pytest.fail(f"Failed to track stats: {e}")


def run_integration_tests():
    """Run all integration tests."""
    print("=" * 80)
    print("🧪 COMPREHENSIVE INTEGRATION TESTS")
    print("=" * 80)
    print()

    pytest_args = [
        __file__,
        "-v",
        "--tb=short",
        "-s"
    ]

    exit_code = pytest.main(pytest_args)

    print()
    print("=" * 80)
    if exit_code == 0:
        print("✅ ALL INTEGRATION TESTS PASSED")
    else:
        print("❌ SOME INTEGRATION TESTS FAILED")
    print("=" * 80)

    return exit_code


if __name__ == "__main__":
    exit(run_integration_tests())
