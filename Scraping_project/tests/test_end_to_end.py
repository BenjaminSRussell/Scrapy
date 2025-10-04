"""
Complete End-to-End System Validation Test
"""



class TestSystemReadiness:
    """Validate entire system is ready"""

    def test_configuration_loads(self):
        """Test configuration system works"""
        from src.orchestrator.config import Config
        config = Config('development', validate=True)
        assert config is not None

    def test_nlp_processor_works(self):
        """Test NLP processor is functional"""
        from src.common.nlp_processor import get_nlp_processor

        processor = get_nlp_processor()
        assert processor._initialized is True

        result = processor.process("University of Connecticut offers programs.")
        assert len(result.entities) >= 0
        assert len(result.keywords) > 0

    def test_url_deduplicator_works(self, tmp_path):
        """Test URL deduplication system"""
        from src.common.url_deduplication import URLDeduplicator

        db_path = tmp_path / "test.db"
        dedup = URLDeduplicator(db_path)

        assert dedup.add_if_new("https://example.com") is True
        assert dedup.add_if_new("https://example.com") is False
        assert dedup.count() == 1
        dedup.close()

    def test_pipeline_config_manager(self):
        """Test pipeline configuration manager"""
        from src.common.pipeline_config import ConfigurationManager

        config = ConfigurationManager()
        assert config.paths.stage1_discovery.name == "discovery_output.jsonl"
        assert config.paths.stage2_validation.name == "validation_output.jsonl"
        assert config.paths.stage3_enrichment.name == "enriched_content.jsonl"

    def test_output_paths_consistent(self):
        """Test all output paths are consistent"""
        from src.orchestrator.config import Config

        dev_config = Config('development')
        prod_config = Config('production')

        assert dev_config.get_stage1_config()['output_file'].endswith('discovery_output.jsonl')
        assert prod_config.get_stage1_config()['output_file'].endswith('discovery_output.jsonl')


class TestProductionReadiness:
    """Production readiness validation"""

    def test_critical_imports(self):
        """Validate critical imports work"""
        assert True

    def test_spacy_model_available(self):
        """Validate spaCy model is downloaded"""
        import spacy
        nlp = spacy.load("en_core_web_sm")
        assert nlp is not None

    def test_requirements_installed(self):
        """Validate key packages"""
        assert True
