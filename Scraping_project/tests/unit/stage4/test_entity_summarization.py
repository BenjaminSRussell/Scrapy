"""Unit tests for Stage 4 Entity Summarization Pipeline.

These tests verify the functionality of:
1. FactAggregator: Extractive fact aggregation and semantic deduplication
2. ChronologicalSorter: Date-based sorting and formatting
3. AbstractiveSummarizer: Abstractive summarization with citations
4. EntitySummaryStorage: Delta Lake storage
5. Stage4EntityWorker: End-to-end pipeline orchestration
"""

import json
from datetime import datetime
from unittest import mock

import pytest


class TestFactAggregator:
    """Test the FactAggregator class."""

    def test_add_document_extracts_sentences(self):
        """Test that add_document correctly extracts sentences."""
        from src.stage4.entity_summarization import FactAggregator

        aggregator = FactAggregator()

        content = (
            "Professor Jane Doe joined UConn in 2020. "
            "She received the NSF CAREER Award in 2021. "
            "Jane Doe published a paper in Nature in 2023."
        )

        aggregator.add_document(
            entity_name="Jane Doe",
            entity_type="person",
            content=content,
            source_url="https://example.com/jane",
            publication_date="2023-01-15",
        )

        # Check that facts were added
        assert "Jane Doe" in aggregator.entity_facts
        facts = aggregator.entity_facts["Jane Doe"]

        # Should extract 3 sentences mentioning "Jane" or "Doe"
        assert len(facts) == 3

        # Verify fact structure
        for fact in facts:
            assert "entity_name" in fact
            assert "fact_text" in fact
            assert "source_url" in fact
            assert "publication_date" in fact

    def test_semantic_deduplication(self):
        """Test semantic deduplication of similar facts."""
        from src.stage4.entity_summarization import FactAggregator

        # Mock the sentence transformer to avoid loading the actual model
        with mock.patch("src.stage4.entity_summarization.SentenceTransformer") as mock_transformer:
            # Setup mock to return dummy embeddings
            mock_model = mock.MagicMock()

            # Create embeddings where first two are very similar, third is different
            import numpy as np

            mock_embeddings = np.array(
                [
                    [1.0, 0.0, 0.0],  # Fact 1
                    [0.99, 0.01, 0.0],  # Fact 2 (very similar to Fact 1)
                    [0.0, 0.0, 1.0],  # Fact 3 (different)
                ]
            )

            mock_model.encode.return_value = mock_embeddings
            mock_transformer.return_value = mock_model

            aggregator = FactAggregator(similarity_threshold=0.85)

            # Manually add duplicate facts
            aggregator.entity_facts["Jane Doe"] = [
                {
                    "entity_name": "Jane Doe",
                    "entity_type": "person",
                    "fact_text": "Professor Jane Doe received the NSF CAREER Award in 2021.",
                    "source_url": "https://example.com/1",
                    "publication_date": datetime(2021, 3, 1),
                    "metadata": {},
                },
                {
                    "entity_name": "Jane Doe",
                    "entity_type": "person",
                    "fact_text": "Jane Doe was awarded the NSF CAREER Award in 2021.",  # Similar
                    "source_url": "https://example.com/2",
                    "publication_date": datetime(2021, 4, 1),
                    "metadata": {},
                },
                {
                    "entity_name": "Jane Doe",
                    "entity_type": "person",
                    "fact_text": "Professor Doe published a groundbreaking paper in Nature.",  # Different
                    "source_url": "https://example.com/3",
                    "publication_date": datetime(2023, 1, 1),
                    "metadata": {},
                },
            ]

            # Deduplicate
            deduplicated = aggregator.deduplicate_facts("Jane Doe")

            # Should cluster first two facts together, third separate
            # Result: 2 unique facts
            assert len(deduplicated) == 2

            # Verify source references are aggregated
            for fact in deduplicated:
                assert "source_references" in fact
                assert len(fact["source_references"]) >= 1

    def test_parse_date_handles_formats(self):
        """Test that _parse_date handles various date formats."""
        from src.stage4.entity_summarization import FactAggregator

        aggregator = FactAggregator()

        # Test datetime object
        dt = datetime(2023, 1, 15)
        assert aggregator._parse_date(dt) == dt

        # Test ISO 8601 string
        iso_str = "2023-01-15T10:30:00Z"
        parsed = aggregator._parse_date(iso_str)
        assert isinstance(parsed, datetime)
        assert parsed.year == 2023
        assert parsed.month == 1

        # Test None
        assert aggregator._parse_date(None) is None


class TestChronologicalSorter:
    """Test the ChronologicalSorter class."""

    def test_sort_facts_ascending(self):
        """Test sorting facts in chronological order (oldest first)."""
        from src.stage4.entity_summarization import ChronologicalSorter

        sorter = ChronologicalSorter()

        facts = [
            {"fact_text": "Fact 3", "publication_date": datetime(2023, 1, 1)},
            {"fact_text": "Fact 1", "publication_date": datetime(2021, 1, 1)},
            {"fact_text": "Fact 2", "publication_date": datetime(2022, 1, 1)},
        ]

        sorted_facts = sorter.sort_facts(facts, descending=False)

        assert sorted_facts[0]["fact_text"] == "Fact 1"
        assert sorted_facts[1]["fact_text"] == "Fact 2"
        assert sorted_facts[2]["fact_text"] == "Fact 3"

    def test_sort_facts_descending(self):
        """Test sorting facts in reverse chronological order (newest first)."""
        from src.stage4.entity_summarization import ChronologicalSorter

        sorter = ChronologicalSorter()

        facts = [
            {"fact_text": "Fact 3", "publication_date": datetime(2023, 1, 1)},
            {"fact_text": "Fact 1", "publication_date": datetime(2021, 1, 1)},
            {"fact_text": "Fact 2", "publication_date": datetime(2022, 1, 1)},
        ]

        sorted_facts = sorter.sort_facts(facts, descending=True)

        assert sorted_facts[0]["fact_text"] == "Fact 3"
        assert sorted_facts[1]["fact_text"] == "Fact 2"
        assert sorted_facts[2]["fact_text"] == "Fact 1"

    def test_prepare_for_summarization_formats_with_dates(self):
        """Test that prepare_for_summarization formats facts with date prefixes."""
        from src.stage4.entity_summarization import ChronologicalSorter

        sorter = ChronologicalSorter()

        facts = [
            {"fact_text": "Received award.", "publication_date": datetime(2021, 3, 15)},
            {"fact_text": "Published paper.", "publication_date": datetime(2023, 6, 1)},
        ]

        formatted = sorter.prepare_for_summarization(facts)

        # Should be in chronological order with date prefixes
        assert "(2021-03-15): Received award." in formatted
        assert "(2023-06-01): Published paper." in formatted

        # Earlier date should appear first
        lines = formatted.split("\n")
        assert "(2021-03-15)" in lines[0]
        assert "(2023-06-01)" in lines[1]


class TestAbstractiveSummarizer:
    """Test the AbstractiveSummarizer class."""

    def test_summarize_with_mock_model(self):
        """Test summarization with mocked transformer model."""
        from src.stage4.entity_summarization import AbstractiveSummarizer

        # Mock the transformers pipeline
        with mock.patch("src.stage4.entity_summarization.pipeline") as mock_pipeline:
            # Setup mock summarizer
            mock_summarizer = mock.MagicMock()
            mock_summarizer.return_value = [{"summary_text": "Mocked summary."}]
            mock_pipeline.return_value = mock_summarizer

            summarizer = AbstractiveSummarizer()

            input_text = "(2021-03-15): Jane Doe received an award.\n(2023-06-01): She published a paper."
            facts = [
                {
                    "fact_text": "Jane Doe received an award.",
                    "publication_date": datetime(2021, 3, 15),
                    "source_references": [
                        {"source_url": "https://example.com/1", "publication_date": datetime(2021, 3, 15)}
                    ],
                },
                {
                    "fact_text": "She published a paper.",
                    "publication_date": datetime(2023, 6, 1),
                    "source_references": [
                        {"source_url": "https://example.com/2", "publication_date": datetime(2023, 6, 1)}
                    ],
                },
            ]

            result = summarizer.summarize(input_text, facts)

            assert "summary_text" in result
            assert "citations" in result
            assert "Mocked summary" in result["summary_text"]

    def test_create_citations_mapping(self):
        """Test citation mapping creation."""
        from src.stage4.entity_summarization import AbstractiveSummarizer

        summarizer = AbstractiveSummarizer()

        facts = [
            {
                "source_references": [
                    {"source_url": "https://example.com/1", "publication_date": datetime(2021, 1, 1)},
                    {"source_url": "https://example.com/2", "publication_date": datetime(2021, 2, 1)},
                ]
            },
            {
                "source_references": [
                    {"source_url": "https://example.com/3", "publication_date": datetime(2022, 1, 1)},
                ]
            },
        ]

        citations = summarizer._create_citations(facts)

        assert len(citations) == 2
        assert 1 in citations
        assert 2 in citations
        assert len(citations[1]) == 2  # First fact has 2 sources
        assert len(citations[2]) == 1  # Second fact has 1 source


class TestEntitySummaryStorage:
    """Test the EntitySummaryStorage class."""

    def test_save_summary_creates_record(self):
        """Test that save_summary creates a properly formatted record."""
        from src.stage4.entity_summarization import EntitySummaryStorage

        # Mock the delta manager
        mock_delta = mock.MagicMock()

        storage = EntitySummaryStorage(delta_manager=mock_delta)

        citations = {
            1: [{"source_url": "https://example.com/1", "publication_date": datetime(2021, 1, 1)}],
            2: [{"source_url": "https://example.com/2", "publication_date": datetime(2023, 1, 1)}],
        }

        facts = [
            {"publication_date": datetime(2021, 1, 1)},
            {"publication_date": datetime(2023, 1, 1)},
        ]

        storage.save_summary(
            entity_name="Jane Doe",
            entity_type="person",
            summary_text="Jane Doe is a professor. [1] [2]",
            citations=citations,
            facts=facts,
        )

        # Verify write was called
        mock_delta.write.assert_called_once()

        # Extract the record that was written
        call_args = mock_delta.write.call_args
        table_name = call_args[0][0]
        records = call_args[0][1]

        assert table_name == "entity_summaries"
        assert len(records) == 1

        record = records[0]
        assert record["entity_name"] == "Jane Doe"
        assert record["entity_type"] == "person"
        assert "Jane Doe is a professor" in record["summary_text"]
        assert record["fact_count"] == 2

        # Verify source_references is JSON-encoded
        source_refs = json.loads(record["source_references"])
        assert "1" in source_refs
        assert "2" in source_refs


class TestStage4EntityWorker:
    """Test the Stage4EntityWorker orchestration."""

    def test_process_documents_end_to_end(self):
        """Test end-to-end processing with mocked models."""
        from src.stage4.entity_summarization import Stage4EntityWorker

        # Mock both embedding and summarization models
        with (
            mock.patch("src.stage4.entity_summarization.SentenceTransformer") as mock_st,
            mock.patch("src.stage4.entity_summarization.pipeline") as mock_pipeline,
        ):
            # Setup mock embedding model
            mock_embedding_model = mock.MagicMock()
            import numpy as np

            # All facts are unique (different embeddings)
            mock_embedding_model.encode.return_value = np.array(
                [
                    [1.0, 0.0],
                    [0.0, 1.0],
                ]
            )
            mock_st.return_value = mock_embedding_model

            # Setup mock summarization model
            mock_summarizer = mock.MagicMock()
            mock_summarizer.return_value = [{"summary_text": "Jane Doe is a professor at UConn."}]
            mock_pipeline.return_value = mock_summarizer

            # Mock Delta Lake storage
            with mock.patch("src.stage4.entity_summarization.get_delta_manager") as mock_get_delta:
                mock_delta = mock.MagicMock()
                mock_get_delta.return_value = mock_delta

                # Initialize worker
                worker = Stage4EntityWorker()

                # Process sample documents
                documents = [
                    {
                        "entity_name": "Jane Doe",
                        "entity_type": "person",
                        "content": "Professor Jane Doe joined UConn in 2020. She received an award in 2021.",
                        "source_url": "https://example.com/1",
                        "publication_date": datetime(2020, 1, 1),
                    },
                    {
                        "entity_name": "Jane Doe",
                        "entity_type": "person",
                        "content": "Dr. Jane Doe was promoted to Full Professor in 2024.",
                        "source_url": "https://example.com/2",
                        "publication_date": datetime(2024, 1, 1),
                    },
                ]

                worker.process_documents(documents)

                # Verify that Delta Lake write was called
                mock_delta.write.assert_called()

                # Extract written record
                call_args = mock_delta.write.call_args
                records = call_args[0][1]

                assert len(records) >= 1
                record = records[0]
                assert record["entity_name"] == "Jane Doe"
                assert record["entity_type"] == "person"
                assert "summary_text" in record


@pytest.mark.integration
class TestIntegration:
    """Integration tests that use real models (slower, marked as integration)."""

    def test_real_models_with_sample_data(self):
        """Test with real embedding and summarization models (requires transformers)."""
        pytest.importorskip("sentence_transformers")
        pytest.importorskip("transformers")

        from src.stage4.entity_summarization import Stage4EntityWorker

        # Use small models for testing
        worker = Stage4EntityWorker(
            embedding_model="sentence-transformers/all-MiniLM-L6-v2",
            summarization_model="facebook/bart-large-cnn",
            similarity_threshold=0.85,
            device=-1,  # CPU
        )

        # Mock Delta Lake to avoid actual writes
        with mock.patch("src.stage4.entity_summarization.get_delta_manager") as mock_get_delta:
            mock_delta = mock.MagicMock()
            mock_get_delta.return_value = mock_delta

            # Re-create worker with mocked delta
            worker.storage.delta = mock_delta

            documents = [
                {
                    "entity_name": "Test Entity",
                    "entity_type": "organization",
                    "content": (
                        "Test Entity was founded in 2020. "
                        "It is a research organization. "
                        "Test Entity has published many papers."
                    ),
                    "source_url": "https://example.com/test",
                    "publication_date": datetime(2020, 1, 1),
                },
            ]

            # This will use real models
            worker.process_documents(documents)

            # Verify processing completed
            mock_delta.write.assert_called()
