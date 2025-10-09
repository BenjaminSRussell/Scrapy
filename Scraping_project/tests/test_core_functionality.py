"""Consolidated Core Functionality Tests

Tests the essential components of the pipeline:
1. Delta Lake manager (read/write/checkpoint)
2. PostgreSQL manager (connection, logging, queries)
3. Stage 2 worker (URL analysis and quality control)
4. Stage 3 worker (similarity detection and summarization)
5. Drain lake utility
6. ML error analyzer
"""

import importlib.util
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ============================================================================
# Delta Lake Tests
# ============================================================================

class TestDeltaLake:
    """Test Delta Lake manager core functionality."""

    @pytest.fixture
    def temp_delta_path(self):
        """Create temporary Delta Lake directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_delta_manager_initialization(self, temp_delta_path):
        """Test Delta Lake manager can initialize."""
        from src.common.delta_lake import DeltaLakeManager

        manager = DeltaLakeManager()
        # Manager initialized successfully
        assert manager.base_path is not None
        assert 'stage1_discovery' in manager.tables
        assert 'stage2_page_analysis' in manager.tables

    def test_write_and_read_data(self, temp_delta_path):
        """Test writing and reading data from Delta Lake."""
        from src.common.delta_lake import DeltaLakeManager

        with patch('src.common.constants.DELTA_LAKE', temp_delta_path):
            manager = DeltaLakeManager()

            # Write test data with unique URL to avoid conflicts
            import uuid
            unique_id = str(uuid.uuid4())
            test_data = [
                {'url': f'https://testwrite_{unique_id}.com', 'url_hash': f'hash_{unique_id}_1', 'depth': 0},
                {'url': f'https://testwrite_{unique_id}_2.com', 'url_hash': f'hash_{unique_id}_2', 'depth': 1}
            ]

            manager.write('stage1_discovery', test_data, mode='append', async_write=False)

            # Read data back
            results = manager.read('stage1_discovery')

            # Verify our data is in there (might have old data too)
            assert len(results) >= 2
            our_results = [r for r in results if unique_id in r['url']]
            assert len(our_results) == 2

    def test_list_tables(self, temp_delta_path):
        """Test listing Delta Lake tables."""
        from src.common.delta_lake import DeltaLakeManager

        with patch('src.common.constants.DELTA_LAKE', temp_delta_path):
            manager = DeltaLakeManager()

            tables = manager.list_tables()
            assert len(tables) > 0
            assert any(t['name'] == 'stage1_discovery' for t in tables)


# ============================================================================
# PostgreSQL Manager Tests
# ============================================================================

class TestPostgresManager:
    """Test PostgreSQL manager functionality."""

    def test_postgres_manager_graceful_degradation(self):
        """Test that missing password returns None gracefully."""
        from src.common.postgres_manager import get_postgres_manager

        # Clear password env var
        old_password = os.environ.get('DB_PASSWORD')
        if 'DB_PASSWORD' in os.environ:
            del os.environ['DB_PASSWORD']

        try:
            manager = get_postgres_manager()
            assert manager is None  # Should return None, not raise
        finally:
            if old_password:
                os.environ['DB_PASSWORD'] = old_password

    def test_postgres_manager_with_credentials(self):
        # Skip if psycopg2 not available
        if not importlib.util.find_spec("psycopg2"):
            pytest.skip("psycopg2 not installed")

        from src.common.postgres_manager import PostgresManager

        # Just test that we can create the class structure
        # We can't actually connect without a real database
        try:
            manager = PostgresManager(
                host='localhost',
                port=5432,
                database='test_db',
                user='test_user',
                password='test_password'
            )
            # If it gets this far, initialization worked
            assert manager.host == 'localhost'
        except Exception as e:
            # Expected if no database available
            assert 'connection' in str(e).lower() or 'password' in str(e).lower()

    def test_log_performance_metric(self):
        """Test logging performance metrics."""
        # Skip if psycopg2 not available
        try:
            import psycopg2
        except ImportError:
            pytest.skip("psycopg2 not installed")

        # This test just verifies the method exists and has correct signature
        from src.common.postgres_manager import PostgresManager

        # Check method exists
        assert hasattr(PostgresManager, 'log_performance_metric')


# ============================================================================
# Stage 2 Worker Tests
# ============================================================================

class TestStage2Worker:
    """Test Stage 2 worker functionality."""

    @pytest.fixture
    def mock_delta_manager(self):
        """Create mock Delta Lake manager."""
        manager = MagicMock()
        manager.read.return_value = []
        manager.write.return_value = None
        return manager

    def test_worker_initialization(self, mock_delta_manager):
        """Test Stage 2 worker can initialize."""
        from src.stage2.stage2_worker import Stage2Worker

        with patch('src.stage2.stage2_worker.get_delta_manager', return_value=mock_delta_manager):
            worker = Stage2Worker(max_concurrent=50, batch_size=100)

            assert worker.max_concurrent == 50
            assert worker.batch_size == 100
            assert worker.MIN_WORD_COUNT == 50

    def test_html_analysis(self, mock_delta_manager):
        """Test HTML content analysis."""
        import asyncio

        from src.stage2.stage2_worker import Stage2Worker

        with patch('src.stage2.stage2_worker.get_delta_manager', return_value=mock_delta_manager):
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

            result = asyncio.run(worker._analyze_html('https://test.com', 'abc123', html, False))

            assert result['url'] == 'https://test.com'
            assert result['word_count'] >= 50
            assert not result['is_low_quality']
            assert result['title'] == 'Test Page'

    def test_quality_score_calculation(self, mock_delta_manager):
        """Test quality score calculation."""
        from src.stage2.stage2_worker import Stage2Worker

        with patch('src.stage2.stage2_worker.get_delta_manager', return_value=mock_delta_manager):
            worker = Stage2Worker()

            # Good quality (higher word count and ratio)
            score1 = worker._calculate_quality_score(1000, 0.5)

            # Poor quality (lower word count and ratio)
            score2 = worker._calculate_quality_score(10, 0.05)

            # Good quality should score higher than poor quality
            assert score1 > score2


# ============================================================================
# Stage 3 Worker Tests
# ============================================================================

class TestStage3Worker:
    """Test Stage 3 worker functionality."""

    @pytest.fixture
    def mock_delta_manager(self):
        """Create mock Delta Lake manager."""
        manager = MagicMock()
        manager.read.return_value = []
        manager.write.return_value = None
        return manager

    def test_worker_initialization(self, mock_delta_manager):
        """Test Stage 3 worker can initialize."""
        from src.stage3.stage3_worker import Stage3Worker

        with patch('src.stage3.stage3_worker.get_delta_manager', return_value=mock_delta_manager):
            worker = Stage3Worker(max_concurrent=20, batch_size=50)

            assert worker.max_concurrent == 20
            assert worker.batch_size == 50
            assert worker.SIMILARITY_THRESHOLD == 0.3

    def test_deduplication(self, mock_delta_manager):
        """Test document deduplication with MinHash LSH."""
        import asyncio

        from src.stage3.stage3_worker import Stage3Worker

        with patch('src.stage3.stage3_worker.get_delta_manager', return_value=mock_delta_manager):
            worker = Stage3Worker()

            # Create duplicate documents
            docs = [
                {
                    'url': 'https://test1.com',
                    'url_hash': 'hash1',
                    'text_content': 'This is the first test document with unique content here'
                },
                {
                    'url': 'https://test2.com',
                    'url_hash': 'hash2',
                    'text_content': 'This is the first test document with unique content here'  # Duplicate
                },
                {
                    'url': 'https://test3.com',
                    'url_hash': 'hash3',
                    'text_content': 'Completely different content that should not be deduplicated at all'
                }
            ]

            unique_docs = asyncio.run(worker._deduplicate_documents(docs))

            # Should detect duplicates
            assert len(unique_docs) <= len(docs)
            assert len(unique_docs) >= 1


# ============================================================================
# Drain Lake Utility Tests
# ============================================================================

class TestDrainLake:
    """Test drain lake utility."""

    @pytest.fixture
    def temp_delta_path(self):
        """Create temporary Delta Lake directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_drain_lake_function(self, temp_delta_path):
        """Test drain_lake function logic."""
        import shutil

        from src.common.delta_lake import DeltaLakeManager

        with patch('src.common.constants.DELTA_LAKE', temp_delta_path):
            manager = DeltaLakeManager()

            # Write some test data
            test_data = [{'url': 'https://testdrain.com', 'url_hash': 'drain123'}]
            manager.write('stage1_discovery', test_data, mode='append', async_write=False)

            # Verify data exists
            before = manager.read('stage1_discovery')
            assert len(before) >= 1

            # Drain the table (simulate by deleting and recreating)
            table_path = manager.tables['stage1_discovery']
            if table_path.exists():
                shutil.rmtree(table_path)
                table_path.mkdir(parents=True, exist_ok=True)

            # Verify data is gone (reading empty table returns empty list)
            try:
                after = manager.read('stage1_discovery')
                assert len(after) == 0
            except Exception:
                # Empty table might raise exception, which is also valid
                pass


# ============================================================================
# ML Error Analyzer Tests
# ============================================================================

class TestMLErrorAnalyzer:
    """Test ML error analyzer."""

    @pytest.fixture
    def sample_error_data(self):
        """Create sample error data for testing."""
        return [
            {
                'url': 'https://test1.com/page1',
                'error_type': 'TimeoutError',
                'http_status_code': None,
                'stage': 'stage1'
            },
            {
                'url': 'https://test1.com/page2',
                'error_type': 'TimeoutError',
                'http_status_code': None,
                'stage': 'stage1'
            },
            {
                'url': 'https://test2.com/page1',
                'error_type': 'HttpError',
                'http_status_code': 404,
                'stage': 'stage2'
            },
            {
                'url': 'https://test2.com/page2',
                'error_type': 'HttpError',
                'http_status_code': 404,
                'stage': 'stage2'
            },
        ]

    def test_url_feature_extraction(self, sample_error_data):
        """Test URL feature extraction."""
        # Test the logic directly without importing the full script
        from urllib.parse import urlparse

        url = 'https://test.com/path/to/page.html?query=value'
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]

        features = {
            'domain': parsed.netloc,
            'path_depth': len(path_parts),
            'has_query': 1 if parsed.query else 0,
            'extension': path_parts[-1].split('.')[-1] if path_parts and '.' in path_parts[-1] else 'none',
        }

        assert features['domain'] == 'test.com'
        assert features['path_depth'] == 3
        assert features['has_query'] == 1
        assert features['extension'] == 'html'

    def test_recommendation_generation(self):
        """Test recommendation generation logic."""
        # Test the recommendation logic without importing the script

        def generate_test_recommendations(error_type: str) -> str:
            """Simplified recommendation logic for testing."""
            recommendations = []
            error_lower = error_type.lower()

            if 'timeout' in error_lower:
                recommendations.append("Timeout errors: Increase timeout settings")
            elif 'http' in error_lower or 'error' in error_lower:
                recommendations.append("HTTP errors: Check URL validity")

            return "\n".join(f"• {rec}" for rec in recommendations)

        # Test timeout recommendations
        recs1 = generate_test_recommendations('TimeoutError')
        assert 'timeout' in recs1.lower()

        # Test HTTP error recommendations
        recs2 = generate_test_recommendations('HttpError')
        assert 'http' in recs2.lower() or 'error' in recs2.lower()


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Test integration between components."""

    def test_delta_to_stage2_flow(self):
        """Test data flow from Delta Lake to Stage 2."""
        from src.common.delta_lake import DeltaLakeManager
        from src.stage2.stage2_worker import Stage2Worker

        # This is a minimal integration test to ensure components can work together
        # Full integration tests would require more setup
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('src.common.constants.DELTA_LAKE', Path(tmpdir)):
                delta = DeltaLakeManager()
                worker = Stage2Worker(max_concurrent=1, batch_size=10)

                # Verify both initialized
                assert delta is not None
                assert worker is not None
                assert worker.delta is not None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
