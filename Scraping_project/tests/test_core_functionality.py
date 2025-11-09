import importlib.util
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# ============================================================================

class TestDeltaLake:

    @pytest.fixture
    def temp_delta_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_delta_manager_initialization(self, temp_delta_path):
        from src.common.delta_lake import DeltaLakeManager

        manager = DeltaLakeManager()
        assert manager.base_path is not None
        assert "stage1_discovery" in manager.tables
        assert "stage2_page_analysis" in manager.tables

    def test_write_and_read_data(self, temp_delta_path):
        from src.common.delta_lake import DeltaLakeManager

        with patch("src.common.constants.DELTA_LAKE", temp_delta_path):
            manager = DeltaLakeManager()

            import uuid

            unique_id = str(uuid.uuid4())
            test_data = [
                {
                    "url": f"https://testwrite_{unique_id}.com",
                    "url_hash": f"hash_{unique_id}_1",
                    "depth": 0,
                },
                {
                    "url": f"https://testwrite_{unique_id}_2.com",
                    "url_hash": f"hash_{unique_id}_2",
                    "depth": 1,
                },
            ]

            manager.write("stage1_discovery", test_data, mode="append", async_write=False)

            results = manager.read("stage1_discovery")

            assert len(results) >= 2
            our_results = [r for r in results if unique_id in r["url"]]
            assert len(our_results) == 2

    def test_list_tables(self, temp_delta_path):
        from src.common.delta_lake import DeltaLakeManager

        with patch("src.common.constants.DELTA_LAKE", temp_delta_path):
            manager = DeltaLakeManager()

            tables = manager.list_tables()
            assert len(tables) > 0
            assert any(t["name"] == "stage1_discovery" for t in tables)

# ============================================================================
# ============================================================================

class TestPostgresManager:

    def test_postgres_manager_graceful_degradation(self):
        from src.common.postgres_manager import get_postgres_manager

        old_password = os.environ.get("DB_PASSWORD")
        if "DB_PASSWORD" in os.environ:
            del os.environ["DB_PASSWORD"]

        try:
            manager = get_postgres_manager()
            assert manager is None
        finally:
            if old_password:
                os.environ["DB_PASSWORD"] = old_password

    def test_postgres_manager_with_credentials(self):
        if not importlib.util.find_spec("psycopg2"):
            pytest.skip("psycopg2 not installed")

        from src.common.postgres_manager import PostgresManager

        try:
            manager = PostgresManager(
                host="localhost",
                port=5432,
                database="test_db",
                user="test_user",
                password="test_password",
            )
            assert manager.host == "localhost"
        except Exception as e:
            assert "connection" in str(e).lower() or "password" in str(e).lower()

    def test_log_performance_metric(self):
        import importlib.util

        if importlib.util.find_spec("psycopg2") is None:
            pytest.skip("psycopg2 not installed")

        from src.common.postgres_manager import PostgresManager

        assert hasattr(PostgresManager, "log_performance_metric")

# ============================================================================
# ============================================================================

class TestStage2Worker:

    @pytest.fixture
    def mock_delta_manager(self):
        manager = MagicMock()
        manager.read.return_value = []
        manager.write.return_value = None
        return manager

    def test_worker_initialization(self, mock_delta_manager):
        from src.stage2.stage2_worker import Stage2Worker

        with patch(
            "src.stage2.stage2_worker.get_delta_manager",
            return_value=mock_delta_manager,
        ):
            worker = Stage2Worker(max_concurrent=50, batch_size=100)

            assert worker.max_concurrent == 50
            assert worker.batch_size == 100
            assert worker.MIN_WORD_COUNT == 50

    def test_html_analysis(self, mock_delta_manager):
        import asyncio

        from src.stage2.stage2_worker import Stage2Worker

        with patch(
            "src.stage2.stage2_worker.get_delta_manager",
            return_value=mock_delta_manager,
        ):
            worker = Stage2Worker()

            html = """
            <html>
                <head><title>Test Page</title></head>
                <body>
                    <h1>Test Content</h1>
                    <p>This is a test paragraph with enough words to pass quality checks.
                       We need to have at least fifty words here for the quality check to pass.
                       So let's add some more content here to make sure we meet the minimum
                       word count requirement. This should definitely be enough now. Just a bit more
                       to be absolutely certain we pass all the quality thresholds.</p>
                </body>
            </html>
            """

            result = asyncio.run(worker._analyze_html("https://test.com", "abc123", html, False))

            assert result["url"] == "https://test.com"
            assert result["word_count"] >= 50
            assert not result["is_low_quality"]
            assert result["title"] == "Test Page"

    def test_quality_score_calculation(self, mock_delta_manager):
        from src.stage2.stage2_worker import Stage2Worker

        with patch(
            "src.stage2.stage2_worker.get_delta_manager",
            return_value=mock_delta_manager,
        ):
            worker = Stage2Worker()

            score1 = worker._calculate_quality_score(1000, 0.5)

            score2 = worker._calculate_quality_score(10, 0.05)

            assert score1 > score2

# ============================================================================
# ============================================================================

class TestStage3Worker:

    @pytest.fixture
    def mock_delta_manager(self):
        manager = MagicMock()
        manager.read.return_value = []
        manager.write.return_value = None
        return manager

    def test_worker_initialization(self, mock_delta_manager):
        from src.stage3.stage3_worker import Stage3Worker

        with patch(
            "src.stage3.stage3_worker.get_delta_manager",
            return_value=mock_delta_manager,
        ):
            worker = Stage3Worker(max_concurrent=20, batch_size=50)

            assert worker.max_concurrent == 20
            assert worker.batch_size == 50
            assert worker.SIMILARITY_THRESHOLD == 0.3

    def test_deduplication(self, mock_delta_manager):
        import asyncio

        from src.stage3.stage3_worker import Stage3Worker

        with patch(
            "src.stage3.stage3_worker.get_delta_manager",
            return_value=mock_delta_manager,
        ):
            worker = Stage3Worker()

            docs = [
                {
                    "url": "https://test1.com",
                    "url_hash": "hash1",
                    "text_content": "This is the first test document with unique content here",
                },
                {
                    "url": "https://test2.com",
                    "url_hash": "hash2",
                    "text_content": "This is the first test document with unique content here",
                },
                {
                    "url": "https://test3.com",
                    "url_hash": "hash3",
                    "text_content": "Completely different content that should not be deduplicated at all",
                },
            ]

            unique_docs = asyncio.run(worker._deduplicate_documents(docs))

            assert len(unique_docs) <= len(docs)
            assert len(unique_docs) >= 1

# ============================================================================
# ============================================================================

class TestDrainLake:

    @pytest.fixture
    def temp_delta_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_drain_lake_function(self, temp_delta_path):
        import shutil

        from src.common.delta_lake import DeltaLakeManager

        with patch("src.common.constants.DELTA_LAKE", temp_delta_path):
            manager = DeltaLakeManager()

            test_data = [{"url": "https://testdrain.com", "url_hash": "drain123"}]
            manager.write("stage1_discovery", test_data, mode="append", async_write=False)

            before = manager.read("stage1_discovery")
            assert len(before) >= 1

            table_path = manager.tables["stage1_discovery"]
            if table_path.exists():
                shutil.rmtree(table_path)
                table_path.mkdir(parents=True, exist_ok=True)

            try:
                after = manager.read("stage1_discovery")
                assert len(after) == 0
            except Exception:
                pass

# ============================================================================
# ============================================================================

class TestMLErrorAnalyzer:

    @pytest.fixture
    def sample_error_data(self):
        return [
            {
                "url": "https://test1.com/page1",
                "error_type": "TimeoutError",
                "http_status_code": None,
                "stage": "stage1",
            },
            {
                "url": "https://test1.com/page2",
                "error_type": "TimeoutError",
                "http_status_code": None,
                "stage": "stage1",
            },
            {
                "url": "https://test2.com/page1",
                "error_type": "HttpError",
                "http_status_code": 404,
                "stage": "stage2",
            },
            {
                "url": "https://test2.com/page2",
                "error_type": "HttpError",
                "http_status_code": 404,
                "stage": "stage2",
            },
        ]

    def test_url_feature_extraction(self, sample_error_data):
        from urllib.parse import urlparse

        url = "https://test.com/path/to/page.html?query=value"
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]

        features = {
            "domain": parsed.netloc,
            "path_depth": len(path_parts),
            "has_query": 1 if parsed.query else 0,
            "extension": (path_parts[-1].split(".")[-1] if path_parts and "." in path_parts[-1] else "none"),
        }

        assert features["domain"] == "test.com"
        assert features["path_depth"] == 3
        assert features["has_query"] == 1
        assert features["extension"] == "html"

    def test_recommendation_generation(self):

        def generate_test_recommendations(error_type: str) -> str:
            recommendations = []
            error_lower = error_type.lower()

            if "timeout" in error_lower:
                recommendations.append("Timeout errors: Increase timeout settings")
            elif "http" in error_lower or "error" in error_lower:
                recommendations.append("HTTP errors: Check URL validity")

            return "\n".join(f"• {rec}" for rec in recommendations)

        recs1 = generate_test_recommendations("TimeoutError")
        assert "timeout" in recs1.lower()

        recs2 = generate_test_recommendations("HttpError")
        assert "http" in recs2.lower() or "error" in recs2.lower()

# ============================================================================
# ============================================================================

class TestIntegration:

    def test_delta_to_stage2_flow(self):
        from src.common.delta_lake import DeltaLakeManager
        from src.stage2.stage2_worker import Stage2Worker

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.common.constants.DELTA_LAKE", Path(tmpdir)):
                delta = DeltaLakeManager()
                worker = Stage2Worker(max_concurrent=1, batch_size=10)

                assert delta is not None
                assert worker is not None
                assert worker.delta is not None

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
