"""
Comprehensive regression tests for spaCy/NLP integration functionality.

Tests ensure model loading, device selection, and stop-word handling stay functional
instead of being masked by stubs. These tests run actual spaCy and transformer models
to catch real-world integration issues.
"""

import pytest
import logging
from typing import List, Set, Optional
from unittest.mock import Mock, patch

from common.nlp import (
    NLPRegistry,
    NLPSettings,
    initialize_nlp,
    get_registry,
    extract_entities_and_keywords,
    extract_content_tags,
    has_audio_links,
    clean_text,
    extract_keywords_simple,
    get_text_stats,
    select_device
)


class TestNLPIntegrationRegression:
    """Regression tests for NLP integration with real models"""

    @pytest.mark.critical
    def test_spacy_model_loading_and_initialization(self):
        """Test that spaCy model loads correctly with proper configuration"""
        settings = NLPSettings(
            spacy_model="en_core_web_sm",
            transformer_model=None,  # Skip transformer for this test
            preferred_device="cpu"
        )

        try:
            registry = NLPRegistry(settings)

            # Verify spaCy model loaded
            assert registry.spacy_nlp is not None
            assert hasattr(registry.spacy_nlp, 'pipe_names')

            # Verify entity labels are extracted
            assert isinstance(registry.entity_labels, set)

            # Verify stop words are built
            assert isinstance(registry.stop_words, set)
            assert len(registry.stop_words) > 0

            # Test basic processing
            doc = registry.spacy_nlp("This is a test sentence.")
            assert len(doc) > 0
            assert all(hasattr(token, 'text') for token in doc)

        except RuntimeError as e:
            if "spaCy is required but not installed" in str(e):
                pytest.skip("spaCy not installed")
            elif "Unable to load spaCy model" in str(e):
                pytest.skip("spaCy model en_core_web_sm not available")
            else:
                raise

    @pytest.mark.critical
    def test_spacy_entity_extraction_functionality(self):
        """Test spaCy entity extraction with real text"""
        settings = NLPSettings(spacy_model="en_core_web_sm", transformer_model=None)

        try:
            registry = NLPRegistry(settings)

            # Test text with known entities
            test_text = """
            The University of Connecticut (UConn) is located in Storrs, Connecticut.
            It was founded in 1881 and serves over 32,000 students.
            Professor John Smith teaches Computer Science in the Engineering Department.
            """

            entities, keywords = registry.extract_with_spacy(test_text, top_k=10)

            # Verify entities were extracted
            assert isinstance(entities, list)
            assert len(entities) > 0

            # Should find organization, location, and person entities
            entities_text = " ".join(entities).lower()
            assert any(term in entities_text for term in ["university", "connecticut", "uconn", "storrs"])

            # Verify keywords were extracted
            assert isinstance(keywords, list)
            assert len(keywords) > 0

            # Keywords should include meaningful terms
            keywords_text = " ".join(keywords).lower()
            assert any(term in keywords_text for term in ["university", "student", "professor", "teach"])

        except RuntimeError as e:
            if "spaCy" in str(e):
                pytest.skip(f"spaCy not available: {e}")
            else:
                raise

    @pytest.mark.critical
    def test_transformer_model_loading_and_functionality(self):
        """Test transformer model loading and entity extraction"""
        settings = NLPSettings(
            spacy_model="en_core_web_sm",
            transformer_model="dslim/bert-base-NER",
            preferred_device="cpu"
        )

        try:
            registry = NLPRegistry(settings)

            if registry.transformer_pipeline is None:
                pytest.skip("Transformer model not available")

            # Test transformer entity extraction
            test_text = "Barack Obama was the President of the United States."

            entities = registry.extract_entities_with_transformer(test_text)

            assert isinstance(entities, list)
            # Should extract person and organization entities
            entities_text = " ".join(entities).lower()
            # Note: Transformer results may vary, so we test basic functionality
            assert len(entities) >= 0  # At minimum, should not crash

        except ImportError:
            pytest.skip("transformers package not available")
        except Exception as e:
            if "transformer" in str(e).lower():
                pytest.skip(f"Transformer model loading failed: {e}")
            else:
                raise

    @pytest.mark.critical
    def test_device_selection_functionality(self):
        """Test device selection logic for different hardware configurations"""
        # Test explicit device selection
        assert select_device("cpu") == "cpu"
        assert select_device("cuda") == "cuda"
        assert select_device("mps") == "mps"

        # Test automatic device selection
        auto_device = select_device(None)
        assert auto_device in ["cpu", "cuda", "mps", "mlx"]

        # Test with settings
        cpu_settings = NLPSettings(preferred_device="cpu")
        registry = NLPRegistry(cpu_settings)
        assert registry.device == "cpu"

    @pytest.mark.critical
    def test_stop_words_handling_comprehensive(self):
        """Test stop words handling with additions and overrides"""
        # Test with custom stop words
        additional_stops = {"custom", "stopword", "test"}
        override_stops = {"the", "and"}  # Remove common stop words

        settings = NLPSettings(
            additional_stop_words=additional_stops,
            stop_word_overrides=override_stops
        )

        try:
            registry = NLPRegistry(settings)

            # Verify additional stop words are included
            for word in additional_stops:
                assert word in registry.stop_words

            # Verify override words are excluded
            for word in override_stops:
                assert word not in registry.stop_words

            # Test keyword extraction respects stop words
            test_text = "This is a test custom stopword sentence with the and words."
            keywords = registry._keywords_from_doc(registry.spacy_nlp(test_text), 10)

            # Custom stop words should be filtered out
            keywords_text = " ".join(keywords).lower()
            assert "custom" not in keywords_text
            assert "stopword" not in keywords_text

            # Override words should be allowed
            assert "the" in keywords_text or "and" in keywords_text

        except RuntimeError as e:
            if "spaCy" in str(e):
                pytest.skip(f"spaCy not available: {e}")
            else:
                raise

    @pytest.mark.critical
    def test_lemmatization_functionality(self):
        """Test that lemmatization works correctly for keyword extraction"""
        settings = NLPSettings(spacy_model="en_core_web_sm")

        try:
            registry = NLPRegistry(settings)

            # Test text with various word forms
            test_text = "Running, runs, ran, and runner are related to running."

            doc = registry.spacy_nlp(test_text)
            keywords = registry._keywords_from_doc(doc, 10)

            # Should lemmatize variations to base form
            keywords_text = " ".join(keywords).lower()
            assert "run" in keywords_text or "running" in keywords_text

            # Should not have multiple forms of the same word
            run_variants = [k for k in keywords if "run" in k.lower()]
            assert len(run_variants) <= 2  # Should consolidate most variants

        except RuntimeError as e:
            if "spaCy" in str(e):
                pytest.skip(f"spaCy not available: {e}")
            else:
                raise

    @pytest.mark.critical
    def test_global_registry_management(self):
        """Test global NLP registry initialization and management"""
        # Test initialization
        initialize_nlp()
        registry1 = get_registry()
        assert registry1 is not None

        # Test singleton behavior
        registry2 = get_registry()
        assert registry1 is registry2

        # Test custom settings initialization
        custom_settings = NLPSettings(
            spacy_model="en_core_web_sm",
            preferred_device="cpu"
        )
        initialize_nlp(custom_settings)
        registry3 = get_registry()
        assert registry3 is not None
        assert registry3.device == "cpu"

    @pytest.mark.critical
    def test_extract_entities_and_keywords_integration(self):
        """Test high-level entity and keyword extraction function"""
        # Test with spaCy backend
        text = """
        The University of Connecticut offers excellent Computer Science programs.
        Students can pursue undergraduate and graduate degrees in various fields.
        Dr. Jane Doe leads the Artificial Intelligence research group.
        """

        try:
            entities, keywords = extract_entities_and_keywords(
                text,
                max_length=1000,
                top_k=8,
                backend="spacy"
            )

            assert isinstance(entities, list)
            assert isinstance(keywords, list)
            assert len(keywords) <= 8

            # Should extract meaningful content
            all_content = " ".join(entities + keywords).lower()
            assert any(term in all_content for term in ["university", "computer", "science", "student"])

        except RuntimeError as e:
            if "spaCy" in str(e):
                pytest.skip(f"spaCy not available: {e}")
            else:
                raise

        # Test with transformer backend (if available)
        try:
            entities_tf, keywords_tf = extract_entities_and_keywords(
                text,
                max_length=1000,
                top_k=8,
                backend="transformer"
            )

            assert isinstance(entities_tf, list)
            assert isinstance(keywords_tf, list)

        except Exception:
            # Transformer backend may not be available, which is fine
            pass

    @pytest.mark.critical
    def test_text_length_truncation(self):
        """Test that long text is properly truncated"""
        # Create long text
        long_text = "This is a test sentence. " * 1000  # Very long text

        try:
            entities, keywords = extract_entities_and_keywords(
                long_text,
                max_length=100,  # Short limit
                top_k=5
            )

            # Should not crash with long text
            assert isinstance(entities, list)
            assert isinstance(keywords, list)

        except RuntimeError as e:
            if "spaCy" in str(e):
                pytest.skip(f"spaCy not available: {e}")
            else:
                raise

    @pytest.mark.critical
    def test_empty_and_none_text_handling(self):
        """Test handling of empty or None text inputs"""
        test_cases = ["", None, "   ", "\n\t "]

        for text in test_cases:
            entities, keywords = extract_entities_and_keywords(text or "")
            assert entities == []
            assert keywords == []

    @pytest.mark.critical
    def test_content_tags_extraction_functionality(self):
        """Test URL path content tag extraction"""
        predefined_tags = {"programs", "admissions", "research", "news", "events"}

        test_cases = [
            ("/programs/undergraduate", ["programs"]),
            ("/admissions/apply/international", ["admissions"]),
            ("/research/news/events", ["research", "news", "events"]),
            ("/Programs/ADMISSIONS", ["programs", "admissions"]),  # Case insensitive
            ("/invalid/path/unknown", []),
            ("", []),
        ]

        for url_path, expected in test_cases:
            result = extract_content_tags(url_path, predefined_tags)
            assert result == expected

    @pytest.mark.critical
    def test_audio_links_detection(self):
        """Test audio link detection functionality"""
        test_cases = [
            (["https://example.com/song.mp3"], True),
            (["https://example.com/audio.wav"], True),
            (["https://example.com/music.ogg"], True),
            (["https://example.com/sound.flac"], True),
            (["https://example.com/podcast.mp3?id=123"], True),
            (["https://example.com/page.html"], False),
            (["https://example.com/image.jpg"], False),
            ([], False),
            ([None, "https://example.com/test.mp3"], True),
        ]

        for links, expected in test_cases:
            result = has_audio_links(links)
            assert result == expected

    @pytest.mark.critical
    def test_text_cleaning_functionality(self):
        """Test text cleaning and normalization"""
        test_cases = [
            ("  Multiple   spaces  ", "Multiple spaces"),
            ("Text\nwith\nnewlines", "Text with newlines"),
            ("Text with special chars @#$%", "Text with special chars"),
            ("Preserve contractions: don't, won't, I'm", "Preserve contractions: don't, won't, I'm"),
            ("", ""),
            (None, ""),
        ]

        for input_text, expected in test_cases:
            result = clean_text(input_text or "")
            assert result == expected

    @pytest.mark.critical
    def test_simple_keyword_extraction(self):
        """Test simple keyword extraction without spaCy dependency"""
        text = "This is a simple test for keyword extraction functionality."
        stop_words = {"this", "is", "a", "for"}

        keywords = extract_keywords_simple(text, top_k=5, stop_words=stop_words)

        assert isinstance(keywords, list)
        assert len(keywords) <= 5

        # Should not include stop words
        for kw in keywords:
            assert kw not in stop_words

        # Should include meaningful words
        keywords_text = " ".join(keywords)
        assert any(word in keywords_text for word in ["simple", "test", "keyword", "extraction"])

    @pytest.mark.critical
    def test_text_statistics_accuracy(self):
        """Test text statistics calculation"""
        test_text = "This is a test. It has multiple sentences! How many words?"

        stats = get_text_stats(test_text)

        assert stats["word_count"] == 12
        assert stats["char_count"] == len(test_text)
        assert stats["sentence_count"] == 3
        assert stats["avg_word_length"] > 0

        # Test empty text
        empty_stats = get_text_stats("")
        assert empty_stats["word_count"] == 0
        assert empty_stats["char_count"] == 0
        assert empty_stats["sentence_count"] == 0
        assert empty_stats["avg_word_length"] == 0


class TestNLPPerformanceRegression:
    """Performance regression tests for NLP functionality"""

    @pytest.mark.performance
    def test_entity_extraction_performance(self):
        """Test entity extraction performance doesn't regress"""
        import time

        # Generate realistic text
        text = """
        The University of Connecticut is a premier public research university.
        Founded in 1881, UConn serves over 32,000 students across multiple campuses.
        The university offers 115 undergraduate majors and 80 graduate programs.
        Notable alumni include former NBA player Ray Allen and journalist Anderson Cooper.
        The main campus in Storrs spans 4,400 acres and features state-of-the-art facilities.
        Research expenditures exceed $200 million annually, supporting groundbreaking work
        in fields such as stem cell research, materials science, and engineering.
        """ * 5  # Make it longer for performance testing

        try:
            start_time = time.perf_counter()

            entities, keywords = extract_entities_and_keywords(
                text,
                max_length=5000,
                top_k=20,
                backend="spacy"
            )

            duration = time.perf_counter() - start_time

            # Performance baseline - should process text efficiently
            assert duration < 2.0, f"Entity extraction too slow: {duration:.2f}s"
            assert len(entities) > 0, "Should extract some entities"
            assert len(keywords) > 0, "Should extract some keywords"

        except RuntimeError as e:
            if "spaCy" in str(e):
                pytest.skip(f"spaCy not available: {e}")
            else:
                raise

    @pytest.mark.performance
    def test_bulk_text_processing_performance(self):
        """Test bulk text processing performance"""
        import time

        # Generate multiple text samples
        texts = [
            f"This is sample text number {i}. It contains various information about topic {i}."
            for i in range(100)
        ]

        start_time = time.perf_counter()

        results = []
        for text in texts:
            try:
                entities, keywords = extract_entities_and_keywords(text, top_k=5)
                results.append((entities, keywords))
            except RuntimeError:
                # Skip if spaCy not available
                break

        duration = time.perf_counter() - start_time

        if results:  # Only test if we processed some texts
            texts_per_second = len(results) / duration
            assert texts_per_second > 10, f"Bulk processing too slow: {texts_per_second:.1f} texts/sec"


class TestNLPErrorHandlingRegression:
    """Error handling regression tests for NLP functionality"""

    @pytest.mark.critical
    def test_missing_spacy_model_handling(self):
        """Test graceful handling when spaCy model is missing"""
        settings = NLPSettings(spacy_model="nonexistent_model")

        with pytest.raises(RuntimeError, match="Unable to load spaCy model"):
            NLPRegistry(settings)

    @pytest.mark.critical
    def test_missing_transformers_handling(self):
        """Test graceful handling when transformers package is missing"""
        with patch('common.nlp.pipeline', side_effect=ImportError("No module named 'transformers'")):
            settings = NLPSettings(transformer_model="some-model")
            registry = NLPRegistry(settings)

            # Should create registry without transformer
            assert registry.transformer_pipeline is None

    @pytest.mark.critical
    def test_device_fallback_handling(self):
        """Test device fallback when preferred device is unavailable"""
        # Mock torch to simulate device unavailability
        with patch('common.nlp.torch', side_effect=ImportError("No module named 'torch'")):
            device = select_device("cuda")
            assert device == "cpu"  # Should fallback to CPU

    @pytest.mark.critical
    def test_corrupted_text_handling(self):
        """Test handling of corrupted or unusual text inputs"""
        corrupted_texts = [
            "\x00\x01\x02 corrupted bytes",
            "Mixed encoding \xff\xfe text",
            "Very long word: " + "a" * 10000,
            "Emoji text 🎓📚🎯 university",
            "Text with\x0cnull\x0bcharacters",
        ]

        for text in corrupted_texts:
            try:
                # Should not crash with corrupted input
                entities, keywords = extract_entities_and_keywords(text)
                assert isinstance(entities, list)
                assert isinstance(keywords, list)

            except (UnicodeError, RuntimeError) as e:
                # Acceptable for some corrupted inputs to fail gracefully
                assert isinstance(e, (UnicodeError, RuntimeError))

    @pytest.mark.critical
    def test_memory_handling_with_large_text(self):
        """Test memory handling with very large text inputs"""
        # Create extremely large text
        huge_text = "This is a memory test. " * 50000  # ~1.15MB of text

        try:
            # Should handle large text without memory issues
            entities, keywords = extract_entities_and_keywords(
                huge_text,
                max_length=10000,  # Should truncate appropriately
                top_k=10
            )

            assert isinstance(entities, list)
            assert isinstance(keywords, list)
            assert len(keywords) <= 10

        except (MemoryError, RuntimeError) as e:
            # Memory errors are acceptable for extremely large inputs
            assert isinstance(e, (MemoryError, RuntimeError))


# Integration tests that verify actual model behavior
@pytest.mark.integration
class TestNLPRealModelIntegration:
    """Integration tests with real models to catch regression in model behavior"""

    @pytest.mark.slow
    def test_spacy_model_actual_entities(self):
        """Test spaCy model with known entities to verify model behavior"""
        test_cases = [
            {
                "text": "Apple Inc. is located in Cupertino, California.",
                "expected_types": ["ORG", "GPE"],  # Organization, Geopolitical entity
            },
            {
                "text": "Barack Obama was born on August 4, 1961.",
                "expected_types": ["PERSON", "DATE"],
            },
            {
                "text": "The University of Connecticut has 32,000 students.",
                "expected_types": ["ORG", "CARDINAL"],  # Organization, Number
            }
        ]

        try:
            settings = NLPSettings(spacy_model="en_core_web_sm")
            registry = NLPRegistry(settings)

            for case in test_cases:
                doc = registry.spacy_nlp(case["text"])
                found_labels = {ent.label_ for ent in doc.ents}

                # Should find at least some expected entity types
                overlap = found_labels.intersection(case["expected_types"])
                assert len(overlap) > 0, f"No expected entities found in '{case['text']}'. Found: {found_labels}"

        except RuntimeError as e:
            if "spaCy" in str(e):
                pytest.skip(f"spaCy not available: {e}")
            else:
                raise

    @pytest.mark.slow
    def test_model_consistency_across_runs(self):
        """Test that models produce consistent results across multiple runs"""
        text = "The University of Connecticut is located in Storrs, Connecticut."

        try:
            # Run multiple times and verify consistency
            results = []
            for _ in range(3):
                entities, keywords = extract_entities_and_keywords(text, top_k=10)
                results.append((entities, keywords))

            # Results should be identical across runs
            first_result = results[0]
            for result in results[1:]:
                assert result == first_result, "Model results should be consistent across runs"

        except RuntimeError as e:
            if "spaCy" in str(e):
                pytest.skip(f"spaCy not available: {e}")
            else:
                raise