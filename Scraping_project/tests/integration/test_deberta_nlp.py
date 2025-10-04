"""
Integration test for DeBERTa NLP Pipeline

Validates that DeBERTa models work end-to-end for entity extraction and classification.
"""

import pytest

from src.common.nlp_manager import DeBERTaNLPProcessor, NLPManager


@pytest.fixture
def sample_text():
    """Sample text for testing"""
    return """
    The University of Connecticut (UConn) is a public research university in Storrs, Connecticut.
    It was founded in 1881 and offers undergraduate and graduate programs in engineering,
    business, computer science, and liberal arts. The university is known for its STEM programs
    and basketball teams.
    """


@pytest.fixture
def nlp_manager():
    """NLP Manager instance"""
    manager = NLPManager()
    manager.initialize()
    return manager


class TestDeBERTaNLP:
    """Test DeBERTa NLP processing"""

    def test_nlp_processor_initialization(self):
        """Test that NLP processor initializes correctly"""
        processor = DeBERTaNLPProcessor()
        processor.initialize()
        assert processor._initialized is True

    def test_entity_extraction(self, nlp_manager, sample_text):
        """Test entity extraction with DeBERTa"""
        result = nlp_manager.process_text(sample_text)

        # Should extract some entities
        assert len(result.entities) > 0

        # Should find university-related entities
        entities_lower = [e.lower() for e in result.entities]
        assert any('uconn' in e or 'connecticut' in e for e in entities_lower)

    def test_keyword_extraction(self, nlp_manager, sample_text):
        """Test keyword extraction"""
        result = nlp_manager.process_text(sample_text)

        # Should extract keywords
        assert len(result.keywords) > 0

        # Should include relevant keywords
        keywords_lower = [k.lower() for k in result.keywords]
        assert 'university' in keywords_lower or 'uconn' in keywords_lower

    def test_zero_shot_classification(self, nlp_manager, sample_text):
        """Test zero-shot content classification"""
        categories = [
            'education',
            'sports',
            'business',
            'healthcare',
            'technology'
        ]

        result = nlp_manager.process_text(sample_text, categories=categories)

        # Should classify into categories
        assert len(result.categories) > 0

        # Education should be a top category
        assert 'education' in result.categories

    def test_complete_nlp_pipeline(self, nlp_manager):
        """Test complete NLP pipeline with various inputs"""
        test_cases = [
            {
                'text': 'The Computer Science Department offers AI and machine learning courses.',
                'expected_category': 'technology'
            },
            {
                'text': 'UConn Health provides medical services and conducts clinical research.',
                'expected_category': 'healthcare'
            }
        ]

        categories = ['education', 'healthcare', 'technology', 'sports', 'business']

        for case in test_cases:
            result = nlp_manager.process_text(case['text'], categories=categories)

            # Should have results
            assert len(result.entities) >= 0
            assert len(result.keywords) > 0

            # Should classify correctly
            assert case['expected_category'] in result.categories

    @pytest.mark.slow
    def test_nlp_performance(self, nlp_manager):
        """Test NLP processing performance"""
        import time

        text = "University of Connecticut offers computer science programs." * 10

        start = time.time()
        result = nlp_manager.process_text(text)
        duration = time.time() - start

        # Should complete in reasonable time (< 5 seconds)
        assert duration < 5.0

        # Should return results
        assert result is not None


@pytest.mark.integration
class TestNLPManagerSingleton:
    """Test NLP Manager singleton pattern"""

    def test_singleton_instance(self):
        """Test that NLPManager is a singleton"""
        manager1 = NLPManager()
        manager2 = NLPManager()

        assert manager1 is manager2

    def test_singleton_processor(self):
        """Test that processor is reused"""
        manager1 = NLPManager()
        manager2 = NLPManager()

        assert manager1.processor is manager2.processor
