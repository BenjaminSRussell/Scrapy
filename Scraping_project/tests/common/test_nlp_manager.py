import pytest
from unittest.mock import patch, MagicMock
from src.common.nlp_manager import DeBERTaNLPProcessor, NLPResult

class TestDeBERTaNLPProcessor:
    @patch('transformers.pipeline')
    def test_classify_content_returns_correct_format(self, mock_pipeline):
        """
        Tests that the classify_content method correctly uses the transformers pipeline
        and returns the data in the expected dictionary format.
        """
        # Arrange
        mock_ner_pipeline = MagicMock()
        mock_zero_shot_pipeline = MagicMock()
        mock_zero_shot_pipeline.return_value = {
            'labels': ['education', 'sports'],
            'scores': [0.9, 0.1]
        }
        # The initialize method calls pipeline() for both NER and zero-shot.
        # We use side_effect to provide a different mock for each call.
        mock_pipeline.side_effect = [mock_ner_pipeline, mock_zero_shot_pipeline]

        processor = DeBERTaNLPProcessor()
        processor.initialize() # This will setup the mocked pipelines

        text = "This is a test text about education."
        candidate_labels = ["education", "sports"]

        # Act
        result = processor.classify_content(text, candidate_labels)

        # Assert
        assert isinstance(result, dict)
        assert "education" in result
        assert "sports" in result
        assert result["education"] == 0.9
        assert result["sports"] == 0.1
        # Check that the zero-shot pipeline was called correctly by the classify_content method
        mock_zero_shot_pipeline.assert_called_once_with(text, candidate_labels, multi_label=True)

    def test_process_method_filters_by_confidence(self):
        """
        Tests that the top-level 'process' method correctly integrates the
        results from 'classify_content' and filters by a confidence threshold.
        """
        # Arrange
        processor = DeBERTaNLPProcessor()
        processor._initialized = True # Skip the actual initialization
        processor._ner_pipeline = MagicMock(return_value=[]) # Mock NER to avoid running it
        # Mock the classify_content method to return scores above and below the threshold
        processor.classify_content = MagicMock(return_value={'education': 0.9, 'sports': 0.4, 'business': 0.6})

        text = "This is a test text about education and business."
        categories = ["education", "sports", "business"]

        # Act
        result = processor.process(text, categories=categories)

        # Assert
        assert isinstance(result, NLPResult)
        # Check that only categories with scores > 0.5 are included
        assert "education" in result.categories
        assert "business" in result.categories
        assert "sports" not in result.categories # score 0.4 < 0.5
        # Check that the confidence scores are passed through correctly
        assert result.confidence_scores == {'education': 0.9, 'sports': 0.4, 'business': 0.6}
        processor.classify_content.assert_called_once_with(text, categories)