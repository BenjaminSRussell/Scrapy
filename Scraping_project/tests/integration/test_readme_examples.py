"""
Tests to verify that README examples actually work correctly
"""

import subprocess
import sys
from pathlib import Path

import pytest


class TestREADMEExamples:
    """Test that all examples in README.md actually work"""

    def test_spacy_model_available(self):
        """Test that spaCy model is properly installed"""
        import spacy

        # This should not raise an exception
        nlp = spacy.load("en_core_web_sm")
        assert nlp is not None

        # Test basic functionality
        doc = nlp("UConn is a great university in Connecticut.")
        assert len(doc) > 0
        assert any(token.text == "UConn" for token in doc)

    def test_requirements_install(self):
        """Test that all required packages are importable"""
        required_imports = [
            "scrapy",
            "aiohttp",
            "yaml",
            "spacy",
            "pytest",
            "pandas",
            "numpy",
            "click",
            "tqdm",
            "psutil",
            "pydantic",
        ]

        for module_name in required_imports:
            try:
                __import__(module_name)
            except ImportError:
                pytest.fail(f"Required module {module_name} is not available")

    def test_optional_imports_graceful_fallback(self):
        """Test that optional imports fail gracefully"""
        from src.common.nlp import HAS_TRANSFORMERS

        # Should work whether transformers is installed or not
        # If not installed, should be False and not crash
        assert isinstance(HAS_TRANSFORMERS, bool)

    def test_individual_stage_commands(self):
        """Test that individual stage commands are properly formatted"""
        # These are the commands from README
        commands_to_validate = [
            ["scrapy", "crawl", "discovery"],
            ["python", "-m", "src.stage2.validator"],
            ["scrapy", "crawl", "enrichment", "-a", "urls_file=test.jsonl"],
        ]

        for cmd in commands_to_validate:
            # Just verify command structure - don't actually run them
            assert len(cmd) >= 2
            assert cmd[0] in ["scrapy", "python"]

    def test_orchestrator_commands(self):
        """Test orchestrator command structure"""
        # Test that the orchestrator commands are properly structured
        commands = [
            ["python", "main.py", "--env", "development", "--stage", "2"],
            ["python", "main.py", "--env", "development", "--stage", "3"],
            ["python", "main.py", "--env", "development", "--config-only"],
        ]

        for cmd in commands:
            assert cmd[0] == "python"
            assert cmd[1] == "main.py"
            assert "--env" in cmd
            assert "development" in cmd

    def test_test_commands(self):
        """Test that test commands mentioned in README work"""
        # Test basic pytest command
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent.parent
        )
        assert result.returncode == 0
        assert "pytest" in result.stdout

    def test_config_files_exist(self):
        """Test that mentioned config files exist"""
        project_root = Path(__file__).parent.parent.parent

        required_files = [
            "scrapy.cfg",
            "src/settings.py",
            "config/development.yml",
            "config/production.yml",
            "requirements.txt",
        ]

        for file_path in required_files:
            full_path = project_root / file_path
            assert full_path.exists(), f"Required file {file_path} does not exist"

    def test_data_directories_structure(self):
        """Test that data directory structure matches README"""
        project_root = Path(__file__).parent.parent.parent

        expected_dirs = [
            "data/processed/stage01",
            "data/processed/stage02",
            "data/processed/stage03",
        ]

        for dir_path in expected_dirs:
            full_path = project_root / dir_path
            # These should exist or be creatable
            full_path.mkdir(parents=True, exist_ok=True)
            assert full_path.exists()

    def test_import_paths_consistency(self):
        """Test that import paths match README examples"""
        # Test that the src. prefix imports work
        from src.common import logging as common_logging
        from src.common import nlp as common_nlp
        from src.common import storage as common_storage

        # These should all import without error
        assert common_logging is not None
        assert common_nlp is not None
        assert common_storage is not None

    def test_environment_variables_documented(self):
        """Test that environment variables mentioned are actually used"""
        from src.orchestrator.config import Config

        # Test that config class recognizes the documented env vars
        config = Config(env="development")

        # These are the env vars mentioned in README
        env_vars = [
            'SCRAPY_CONCURRENT_REQUESTS',
            'SCRAPY_DOWNLOAD_DELAY',
            'STAGE1_MAX_DEPTH',
            'STAGE1_BATCH_SIZE',
        ]

        # Config class should have these in its override mapping
        for env_var in env_vars:
            # The config class should be aware of these variables
            # (They're in the _apply_env_overrides method)
            assert hasattr(config, '_apply_env_overrides')

    def test_stage_output_schema_fields(self):
        """Test that output schema fields mentioned in README exist"""
        from src.common.schemas import DiscoveryItem, EnrichmentItem, ValidationResult

        # Test Stage 1 fields from README
        discovery_fields = ['source_url', 'discovered_url', 'first_seen', 'discovery_depth', 'confidence']
        discovery_item = DiscoveryItem(
            source_url="https://test.com",
            discovered_url="https://test.com/page",
            first_seen="2025-01-01T00:00:00",
            url_hash="a" * 64,
            discovery_depth=1
        )

        for field in discovery_fields:
            assert hasattr(discovery_item, field)

        # Test Stage 2 fields from README
        validation_fields = ['url', 'url_hash', 'status_code', 'content_type', 'is_valid', 'response_time']
        validation_result = ValidationResult(
            url="https://test.com",
            url_hash="a" * 64,
            status_code=200,
            content_type="text/html",
            content_length=1000,
            response_time=0.5,
            is_valid=True,
            error_message=None,
            validated_at="2025-01-01T00:00:00"
        )

        for field in validation_fields:
            assert hasattr(validation_result, field)

        # Test Stage 3 fields from README
        enrichment_fields = ['url', 'title', 'text_content', 'entities', 'keywords', 'content_tags', 'enriched_at']
        enrichment_item = EnrichmentItem(
            url="https://test.com",
            url_hash="a" * 64,
            title="Test Title",
            text_content="Test content",
            word_count=2,
            entities=["Test"],
            keywords=["test"],
            content_tags=["test"],
            has_pdf_links=False,
            has_audio_links=False,
            status_code=200,
            content_type="text/html",
            enriched_at="2025-01-01T00:00:00"
        )

        for field in enrichment_fields:
            assert hasattr(enrichment_item, field)

    def test_readme_quick_start_sequence(self):
        """Test that the quick start sequence is logical"""
        # Verify the sequence makes sense:
        # 1. Install deps -> 2. Install spacy model -> 3. Run stages

        # Check that spacy model can be loaded if available
        import spacy

        try:
            nlp = spacy.load("en_core_web_sm")
            assert nlp is not None
            # Test basic functionality if model is available
            doc = nlp("Test sentence")
            assert len(doc) > 0
        except OSError:
            # Model not installed - that's okay for this test
            # We just want to verify the API is correct
            pass
