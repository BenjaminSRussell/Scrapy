"""Comprehensive tests for NLP components - every detail"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from typing import List, Tuple, Set, Optional

from common.nlp import (
    NLPSettings,
    NLPRegistry,
    _DummyNLPRegistry,
    initialize_nlp,
    get_registry,
    extract_entities_and_keywords,
    extract_content_tags,
    has_audio_links,
    clean_text,
    extract_keywords_simple,
    get_text_stats,
    select_device,
    MAX_TEXT_LENGTH,
    TOP_KEYWORDS
)


class TestNLPSettings:
    """settings testing because configuration is everything"""

    def test_default_settings(self):
        settings = NLPSettings()
        assert settings.spacy_model == "en_core_web_sm"
        assert settings.transformer_model == "dslim/bert-base-NER"
        assert settings.preferred_device is None
        assert settings.additional_stop_words == set()
        assert settings.stop_word_overrides == set()

    def test_custom_settings(self):
        settings = NLPSettings(
            spacy_model="en_core_web_lg",
            transformer_model="custom/model",
            preferred_device="cuda",
            additional_stop_words={"custom", "words"},
            stop_word_overrides={"the", "a"}
        )
        assert settings.spacy_model == "en_core_web_lg"
        assert settings.transformer_model == "custom/model"
        assert settings.preferred_device == "cuda"
        assert "custom" in settings.additional_stop_words
        assert "the" in settings.stop_word_overrides

    def test_settings_with_none_values(self):
        settings = NLPSettings(
            transformer_model=None,
            preferred_device=None
        )
        assert settings.transformer_model is None
        assert settings.preferred_device is None

    def test_settings_immutability(self):
        settings = NLPSettings()
        # default factory should create new sets
        settings1 = NLPSettings()
        settings2 = NLPSettings()
        assert settings1.additional_stop_words is not settings2.additional_stop_words


class TestDeviceSelection:
    """device selection testing because hardware matters"""

    def test_select_device_preferred(self):
        result = select_device("mps")
        assert result == "mps"

    def test_select_device_preferred_none(self):
        with patch('common.nlp.torch', create=True) as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            result = select_device(None)
            assert result == "cuda"

    def test_select_device_cuda_available(self):
        with patch('common.nlp.torch', create=True) as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            result = select_device()
            assert result == "cuda"

    def test_select_device_mps_available(self):
        with patch('common.nlp.torch', create=True) as mock_torch:
            mock_torch.cuda.is_available.return_value = False
            mock_backends = Mock()
            mock_mps = Mock()
            mock_mps.is_available.return_value = True
            mock_backends.mps = mock_mps
            mock_torch.backends = mock_backends

            result = select_device()
            assert result == "mps"

    def test_select_device_mlx_available(self):
        with patch('common.nlp.torch', create=True) as mock_torch:
            mock_torch.cuda.is_available.return_value = False
            mock_backends = Mock()
            mock_mps = Mock()
            mock_mps.is_available.return_value = False
            mock_backends.mps = mock_mps
            mock_torch.backends = mock_backends

            with patch('common.nlp.mx', create=True) as mock_mx:
                mock_mx.gpu_count.return_value = 1
                result = select_device()
                assert result == "mlx"

    def test_select_device_cpu_fallback(self):
        with patch('common.nlp.torch', side_effect=ImportError):
            with patch('common.nlp.mx', side_effect=ImportError, create=True):
                result = select_device()
                assert result == "cpu"

    def test_select_device_no_torch(self):
        with patch('common.nlp.torch', side_effect=ImportError):
            result = select_device()
            assert result == "cpu"

    def test_select_device_mlx_edge_cases(self):
        with patch('common.nlp.torch', create=True) as mock_torch:
            mock_torch.cuda.is_available.return_value = False
            mock_backends = Mock()
            mock_mps = Mock()
            mock_mps.is_available.return_value = False
            mock_backends.mps = mock_mps
            mock_torch.backends = mock_backends

            with patch('common.nlp.mx', create=True) as mock_mx:
                # test various MLX edge cases
                mock_mx.gpu_count.return_value = 0
                mock_device = Mock()
                mock_device.type = "gpu"
                mock_mx.default_device.return_value = mock_device
                result = select_device()
                assert result == "mlx"


class TestNLPRegistry:
    """registry testing because centralized management is key"""

    def test_registry_initialization_success(self):
        settings = NLPSettings()

        with patch('common.nlp.spacy') as mock_spacy:
            mock_nlp = Mock()
            mock_nlp.pipe_names = ["tagger", "parser", "ner"]
            mock_nlp.pipe_labels = {"ner": ["PERSON", "ORG"]}
            mock_nlp.Defaults.stop_words = {"the", "a", "an"}
            mock_spacy.load.return_value = mock_nlp

            with patch('common.nlp.select_device', return_value="cpu"):
                registry = NLPRegistry(settings)

                assert registry.spacy_nlp == mock_nlp
                assert "PERSON" in registry.entity_labels
                assert "the" in registry.stop_words

    def test_registry_spacy_not_available(self):
        settings = NLPSettings()

        with patch('common.nlp.spacy', side_effect=ImportError("spaCy not installed")):
            with pytest.raises(RuntimeError, match="spaCy is required"):
                NLPRegistry(settings)

    def test_registry_spacy_model_not_found(self):
        settings = NLPSettings(spacy_model="nonexistent_model")

        with patch('common.nlp.spacy') as mock_spacy:
            mock_spacy.load.side_effect = OSError("Model not found")

            with pytest.raises(RuntimeError, match="Unable to load spaCy model"):
                NLPRegistry(settings)

    def test_registry_lemmatizer_addition(self):
        settings = NLPSettings()

        with patch('common.nlp.spacy') as mock_spacy:
            mock_nlp = Mock()
            mock_nlp.pipe_names = ["tagger", "parser"]  # no lemmatizer
            mock_nlp.pipe_labels = {}
            mock_nlp.Defaults.stop_words = set()
            mock_spacy.load.return_value = mock_nlp

            with patch('common.nlp.select_device', return_value="cpu"):
                NLPRegistry(settings)

                # should attempt to add lemmatizer
                mock_nlp.add_pipe.assert_called_with(
                    "lemmatizer",
                    config={"mode": "rule"},
                    after="tagger"
                )

    def test_registry_lemmatizer_addition_fails(self):
        settings = NLPSettings()

        with patch('common.nlp.spacy') as mock_spacy:
            mock_nlp = Mock()
            mock_nlp.pipe_names = []
            mock_nlp.pipe_labels = {}
            mock_nlp.Defaults.stop_words = set()
            mock_nlp.add_pipe.side_effect = Exception("Failed to add lemmatizer")
            mock_spacy.load.return_value = mock_nlp

            with patch('common.nlp.select_device', return_value="cpu"):
                # should not crash on lemmatizer failure
                registry = NLPRegistry(settings)
                assert registry.spacy_nlp == mock_nlp

    def test_registry_transformer_loading_success(self):
        settings = NLPSettings(transformer_model="test/model")

        with patch('common.nlp.spacy') as mock_spacy:
            mock_nlp = Mock()
            mock_nlp.pipe_names = ["ner"]
            mock_nlp.pipe_labels = {}
            mock_nlp.Defaults.stop_words = set()
            mock_spacy.load.return_value = mock_nlp

            with patch('common.nlp.pipeline') as mock_pipeline:
                mock_transformer = Mock()
                mock_pipeline.return_value = mock_transformer

                with patch('common.nlp.select_device', return_value="cpu"):
                    registry = NLPRegistry(settings)

                    assert registry.transformer_pipeline == mock_transformer
                    mock_pipeline.assert_called_with(
                        "token-classification",
                        model="test/model",
                        tokenizer="test/model",
                        aggregation_strategy="simple",
                        device=-1
                    )

    def test_registry_transformer_not_available(self):
        settings = NLPSettings(transformer_model="test/model")

        with patch('common.nlp.spacy') as mock_spacy:
            mock_nlp = Mock()
            mock_nlp.pipe_names = []
            mock_nlp.pipe_labels = {}
            mock_nlp.Defaults.stop_words = set()
            mock_spacy.load.return_value = mock_nlp

            with patch('common.nlp.pipeline', side_effect=ImportError("transformers not available")):
                with patch('common.nlp.select_device', return_value="cpu"):
                    registry = NLPRegistry(settings)

                    assert registry.transformer_pipeline is None

    def test_registry_transformer_model_loading_fails(self):
        settings = NLPSettings(transformer_model="bad/model")

        with patch('common.nlp.spacy') as mock_spacy:
            mock_nlp = Mock()
            mock_nlp.pipe_names = []
            mock_nlp.pipe_labels = {}
            mock_nlp.Defaults.stop_words = set()
            mock_spacy.load.return_value = mock_nlp

            with patch('common.nlp.pipeline') as mock_pipeline:
                mock_pipeline.side_effect = Exception("Model loading failed")

                with patch('common.nlp.select_device', return_value="cpu"):
                    registry = NLPRegistry(settings)

                    assert registry.transformer_pipeline is None

    def test_registry_device_argument_mapping(self):
        settings = NLPSettings(transformer_model="test/model")

        with patch('common.nlp.spacy') as mock_spacy:
            mock_nlp = Mock()
            mock_nlp.pipe_names = []
            mock_nlp.pipe_labels = {}
            mock_nlp.Defaults.stop_words = set()
            mock_spacy.load.return_value = mock_nlp

            with patch('common.nlp.pipeline') as mock_pipeline:
                # test different device mappings
                test_cases = [
                    ("cuda", 0),
                    ("mps", None),  # will be torch.device("mps")
                    ("cpu", -1),
                    ("mlx", -1)
                ]

                for device, expected_arg in test_cases:
                    with patch('common.nlp.select_device', return_value=device):
                        if device == "mps":
                            with patch('common.nlp.torch', create=True) as mock_torch:
                                mock_device = Mock()
                                mock_torch.device.return_value = mock_device
                                registry = NLPRegistry(settings)

                                if mock_pipeline.called:
                                    # should use torch.device("mps")
                                    assert mock_pipeline.call_args[1]["device"] == mock_device
                        else:
                            registry = NLPRegistry(settings)

                            if mock_pipeline.called:
                                assert mock_pipeline.call_args[1]["device"] == expected_arg

    def test_registry_stop_words_building(self):
        settings = NLPSettings(
            additional_stop_words={"custom", "words"},
            stop_word_overrides={"the"}
        )

        with patch('common.nlp.spacy') as mock_spacy:
            mock_nlp = Mock()
            mock_nlp.pipe_names = []
            mock_nlp.pipe_labels = {}
            mock_nlp.Defaults.stop_words = {"the", "a", "an"}
            mock_spacy.load.return_value = mock_nlp

            with patch('common.nlp.select_device', return_value="cpu"):
                registry = NLPRegistry(settings)

                # should include default + additional - overrides
                assert "a" in registry.stop_words
                assert "an" in registry.stop_words
                assert "custom" in registry.stop_words
                assert "words" in registry.stop_words
                assert "the" not in registry.stop_words  # overridden


class TestSpacyExtraction:
    """spaCy extraction testing because NLP is complex"""

    def setup_method(self):
        self.mock_nlp = Mock()
        self.registry = Mock()
        self.registry.spacy_nlp = self.mock_nlp
        self.registry.entity_labels = {"PERSON", "ORG", "GPE"}
        self.registry.stop_words = {"the", "a", "an", "and", "or"}

    def test_extract_with_spacy_basic(self):
        # mock spaCy doc
        mock_doc = Mock()
        mock_entities = [
            Mock(text="John Doe", label_="PERSON"),
            Mock(text="University of Connecticut", label_="ORG"),
            Mock(text="Connecticut", label_="GPE")
        ]
        mock_doc.ents = mock_entities

        # mock tokens for keywords
        mock_tokens = [
            Mock(is_alpha=True, lemma_="university", text="University"),
            Mock(is_alpha=True, lemma_="student", text="students"),
            Mock(is_alpha=True, lemma_="research", text="research"),
            Mock(is_alpha=False, lemma_="123", text="123"),  # should skip
            Mock(is_alpha=True, lemma_="the", text="the"),  # stop word
        ]
        mock_doc.__iter__ = Mock(return_value=iter(mock_tokens))

        self.mock_nlp.return_value = mock_doc

        # test extraction
        from common.nlp import NLPRegistry
        registry = self.registry
        registry._keywords_from_doc = NLPRegistry._keywords_from_doc.__get__(registry)

        entities, keywords = registry.extract_with_spacy("Test text", 5)

        assert "John Doe" in entities
        assert "University of Connecticut" in entities
        assert "Connecticut" in entities
        assert len(keywords) <= 5

    def test_extract_with_spacy_duplicate_entities(self):
        mock_doc = Mock()
        mock_entities = [
            Mock(text="John Doe", label_="PERSON"),
            Mock(text="John Doe", label_="PERSON"),  # duplicate
            Mock(text="  John Doe  ", label_="PERSON"),  # whitespace variant
        ]
        mock_doc.ents = mock_entities
        mock_doc.__iter__ = Mock(return_value=iter([]))

        self.mock_nlp.return_value = mock_doc

        from common.nlp import NLPRegistry
        registry = self.registry
        registry._keywords_from_doc = NLPRegistry._keywords_from_doc.__get__(registry)

        entities, keywords = registry.extract_with_spacy("Test text", 5)

        # should deduplicate
        assert entities.count("John Doe") == 1

    def test_extract_with_spacy_empty_entities(self):
        mock_doc = Mock()
        mock_entities = [
            Mock(text="", label_="PERSON"),  # empty
            Mock(text="   ", label_="ORG"),  # whitespace only
            Mock(text="Valid Entity", label_="GPE"),
        ]
        mock_doc.ents = mock_entities
        mock_doc.__iter__ = Mock(return_value=iter([]))

        self.mock_nlp.return_value = mock_doc

        from common.nlp import NLPRegistry
        registry = self.registry
        registry._keywords_from_doc = NLPRegistry._keywords_from_doc.__get__(registry)

        entities, keywords = registry.extract_with_spacy("Test text", 5)

        # should skip empty entities
        assert "" not in entities
        assert "   " not in entities
        assert "Valid Entity" in entities

    def test_extract_with_spacy_filtered_labels(self):
        mock_doc = Mock()
        mock_entities = [
            Mock(text="John Doe", label_="PERSON"),
            Mock(text="Some Date", label_="DATE"),  # not in allowed labels
            Mock(text="UConn", label_="ORG"),
        ]
        mock_doc.ents = mock_entities
        mock_doc.__iter__ = Mock(return_value=iter([]))

        self.mock_nlp.return_value = mock_doc

        from common.nlp import NLPRegistry
        registry = self.registry
        registry._keywords_from_doc = NLPRegistry._keywords_from_doc.__get__(registry)

        entities, keywords = registry.extract_with_spacy("Test text", 5)

        # should filter by entity_labels
        assert "John Doe" in entities
        assert "UConn" in entities
        assert "Some Date" not in entities

    def test_keywords_from_doc(self):
        # test keyword extraction logic
        mock_tokens = [
            Mock(is_alpha=True, lemma_="university", text="University"),
            Mock(is_alpha=True, lemma_="student", text="students"),
            Mock(is_alpha=True, lemma_="student", text="student"),  # duplicate lemma
            Mock(is_alpha=True, lemma_="research", text="research"),
            Mock(is_alpha=True, lemma_="university", text="universities"),  # another duplicate
            Mock(is_alpha=False, lemma_="123", text="123"),  # non-alpha
            Mock(is_alpha=True, lemma_="the", text="the"),  # stop word
            Mock(is_alpha=True, lemma_="", text="empty"),  # empty lemma
            Mock(is_alpha=True, lemma_=None, text="none"),  # None lemma
        ]

        from common.nlp import NLPRegistry
        registry = self.registry
        registry._keywords_from_doc = NLPRegistry._keywords_from_doc.__get__(registry)

        keywords = registry._keywords_from_doc(mock_tokens, 3)

        # should return top keywords by frequency
        assert len(keywords) <= 3
        assert "university" in keywords  # appears twice
        assert "student" in keywords
        assert "the" not in keywords  # stop word
        assert "" not in keywords  # empty lemma


class TestTransformerExtraction:
    """transformer extraction testing because transformers are fancy"""

    def setup_method(self):
        self.registry = Mock()
        self.mock_pipeline = Mock()
        self.registry.transformer_pipeline = self.mock_pipeline

    def test_extract_entities_with_transformer_success(self):
        # mock transformer output
        self.mock_pipeline.return_value = [
            {"word": "John Doe", "entity": "PERSON"},
            {"word": "UConn", "entity": "ORG"},
            {"word": "Connecticut", "entity": "LOC"}
        ]

        from common.nlp import NLPRegistry
        registry = self.registry
        registry.extract_entities_with_transformer = NLPRegistry.extract_entities_with_transformer.__get__(registry)

        entities = registry.extract_entities_with_transformer("Test text")

        assert "John Doe" in entities
        assert "UConn" in entities
        assert "Connecticut" in entities

    def test_extract_entities_with_transformer_no_pipeline(self):
        self.registry.transformer_pipeline = None

        from common.nlp import NLPRegistry
        registry = self.registry
        registry.extract_entities_with_transformer = NLPRegistry.extract_entities_with_transformer.__get__(registry)

        with pytest.raises(RuntimeError, match="Transformer pipeline is not initialised"):
            registry.extract_entities_with_transformer("Test text")

    def test_extract_entities_with_transformer_duplicates(self):
        self.mock_pipeline.return_value = [
            {"word": "John Doe", "entity": "PERSON"},
            {"word": "John Doe", "entity": "PERSON"},  # duplicate
            {"word": "  John Doe  ", "entity": "PERSON"},  # whitespace
        ]

        from common.nlp import NLPRegistry
        registry = self.registry
        registry.extract_entities_with_transformer = NLPRegistry.extract_entities_with_transformer.__get__(registry)

        entities = registry.extract_entities_with_transformer("Test text")

        # should deduplicate
        assert entities.count("John Doe") == 1

    def test_extract_entities_with_transformer_empty_results(self):
        self.mock_pipeline.return_value = [
            {"word": "", "entity": "PERSON"},  # empty
            {"word": None, "entity": "ORG"},  # None
            {"entity": "LOC"},  # missing word
            {"word": "Valid", "entity": "PERSON"},
        ]

        from common.nlp import NLPRegistry
        registry = self.registry
        registry.extract_entities_with_transformer = NLPRegistry.extract_entities_with_transformer.__get__(registry)

        entities = registry.extract_entities_with_transformer("Test text")

        # should skip invalid entries
        assert "Valid" in entities
        assert "" not in entities
        assert None not in entities

    def test_extract_entities_with_transformer_alternative_format(self):
        # test alternative output format with "entity" field
        self.mock_pipeline.return_value = [
            {"entity": "John Doe"},  # no "word" field
            {"word": "UConn", "entity": "ORG"},  # has both
        ]

        from common.nlp import NLPRegistry
        registry = self.registry
        registry.extract_entities_with_transformer = NLPRegistry.extract_entities_with_transformer.__get__(registry)

        entities = registry.extract_entities_with_transformer("Test text")

        assert "John Doe" in entities
        assert "UConn" in entities


class TestDummyNLPRegistry:
    """dummy registry testing because fallbacks matter"""

    def test_dummy_registry_creation(self):
        dummy = _DummyNLPRegistry()
        assert dummy is not None

    def test_dummy_extract_with_spacy(self):
        dummy = _DummyNLPRegistry()
        entities, keywords = dummy.extract_with_spacy("any text", 10)
        assert entities == []
        assert keywords == []

    def test_dummy_extract_entities_with_transformer(self):
        dummy = _DummyNLPRegistry()
        entities = dummy.extract_entities_with_transformer("any text")
        assert entities == []


class TestModuleLevelFunctions:
    """module level function testing because globals are tricky"""

    def teardown_method(self):
        # reset global registry
        import common.nlp
        common.nlp.NLP_REGISTRY = None

    def test_initialize_nlp_success(self):
        with patch('common.nlp.NLPRegistry') as mock_registry_class:
            mock_registry = Mock()
            mock_registry_class.return_value = mock_registry

            initialize_nlp()

            mock_registry_class.assert_called_once()
            assert get_registry() == mock_registry

    def test_initialize_nlp_with_custom_settings(self):
        settings = NLPSettings(spacy_model="custom_model")

        with patch('common.nlp.NLPRegistry') as mock_registry_class:
            mock_registry = Mock()
            mock_registry_class.return_value = mock_registry

            initialize_nlp(settings)

            mock_registry_class.assert_called_once_with(settings)

    def test_initialize_nlp_runtime_error(self):
        with patch('common.nlp.NLPRegistry', side_effect=RuntimeError("Model not found")):
            with patch('common.nlp.logger') as mock_logger:
                initialize_nlp()

                mock_logger.warning.assert_called()
                # should fall back to dummy registry
                registry = get_registry()
                assert isinstance(registry, _DummyNLPRegistry)

    def test_get_registry_lazy_initialization(self):
        # ensure registry is None initially
        import common.nlp
        common.nlp.NLP_REGISTRY = None

        with patch('common.nlp.initialize_nlp') as mock_init:
            get_registry()
            mock_init.assert_called_once()

    def test_get_registry_existing_registry(self):
        mock_registry = Mock()
        import common.nlp
        common.nlp.NLP_REGISTRY = mock_registry

        result = get_registry()
        assert result == mock_registry

    def test_extract_entities_and_keywords_empty_text(self):
        entities, keywords = extract_entities_and_keywords("")
        assert entities == []
        assert keywords == []

    def test_extract_entities_and_keywords_none_text(self):
        entities, keywords = extract_entities_and_keywords(None)
        assert entities == []
        assert keywords == []

    def test_extract_entities_and_keywords_spacy_backend(self):
        mock_registry = Mock()
        mock_registry.extract_with_spacy.return_value = (["Entity1"], ["keyword1"])

        with patch('common.nlp.get_registry', return_value=mock_registry):
            entities, keywords = extract_entities_and_keywords("test text", backend="spacy")

            mock_registry.extract_with_spacy.assert_called_once_with("test text", TOP_KEYWORDS)
            assert entities == ["Entity1"]
            assert keywords == ["keyword1"]

    def test_extract_entities_and_keywords_transformer_backend(self):
        mock_registry = Mock()
        mock_registry.extract_entities_with_transformer.return_value = ["TransformerEntity"]
        mock_registry.extract_with_spacy.return_value = ([], ["keyword1"])

        with patch('common.nlp.get_registry', return_value=mock_registry):
            entities, keywords = extract_entities_and_keywords("test text", backend="transformer")

            mock_registry.extract_entities_with_transformer.assert_called_once_with("test text")
            mock_registry.extract_with_spacy.assert_called_once_with("test text", TOP_KEYWORDS)
            assert entities == ["TransformerEntity"]
            assert keywords == ["keyword1"]

    def test_extract_entities_and_keywords_text_truncation(self):
        long_text = "x" * (MAX_TEXT_LENGTH + 1000)
        mock_registry = Mock()
        mock_registry.extract_with_spacy.return_value = ([], [])

        with patch('common.nlp.get_registry', return_value=mock_registry):
            extract_entities_and_keywords(long_text)

            # should truncate to MAX_TEXT_LENGTH
            called_text = mock_registry.extract_with_spacy.call_args[0][0]
            assert len(called_text) == MAX_TEXT_LENGTH

    def test_extract_entities_and_keywords_custom_params(self):
        mock_registry = Mock()
        mock_registry.extract_with_spacy.return_value = ([], [])

        with patch('common.nlp.get_registry', return_value=mock_registry):
            extract_entities_and_keywords("test", max_length=1000, top_k=20)

            mock_registry.extract_with_spacy.assert_called_once_with("test", 20)


class TestContentTagExtraction:
    """content tag extraction testing because categorization matters"""

    def test_extract_content_tags_basic(self):
        url_path = "/academics/undergraduate/computer-science"
        predefined_tags = {"academics", "undergraduate", "computer-science", "graduate"}

        result = extract_content_tags(url_path, predefined_tags)

        assert "academics" in result
        assert "undergraduate" in result
        assert "computer-science" in result
        assert "graduate" not in result

    def test_extract_content_tags_empty_path(self):
        result = extract_content_tags("", {"tag1", "tag2"})
        assert result == []

    def test_extract_content_tags_none_path(self):
        result = extract_content_tags(None, {"tag1", "tag2"})
        assert result == []

    def test_extract_content_tags_empty_predefined(self):
        result = extract_content_tags("/some/path", set())
        assert result == []

    def test_extract_content_tags_none_predefined(self):
        result = extract_content_tags("/some/path", None)
        assert result == []

    def test_extract_content_tags_case_insensitive(self):
        url_path = "/Academics/UnderGraduate"
        predefined_tags = {"academics", "undergraduate"}

        result = extract_content_tags(url_path, predefined_tags)

        assert "academics" in result
        assert "undergraduate" in result

    def test_extract_content_tags_order_preservation(self):
        url_path = "/third/first/second"
        predefined_tags = {"first", "second", "third"}

        result = extract_content_tags(url_path, predefined_tags)

        # should preserve order from URL
        assert result == ["third", "first", "second"]

    def test_extract_content_tags_duplicates_removed(self):
        url_path = "/academics/programs/academics/courses"
        predefined_tags = {"academics", "programs", "courses"}

        result = extract_content_tags(url_path, predefined_tags)

        # should only include "academics" once
        assert result.count("academics") == 1
        assert "programs" in result
        assert "courses" in result

    def test_extract_content_tags_whitespace_handling(self):
        url_path = "/  academics  /  programs  /"
        predefined_tags = {"academics", "programs"}

        result = extract_content_tags(url_path, predefined_tags)

        assert "academics" in result
        assert "programs" in result

    def test_extract_content_tags_special_characters(self):
        url_path = "/computer-science/web-development/full-stack"
        predefined_tags = {"computer-science", "web-development", "full-stack"}

        result = extract_content_tags(url_path, predefined_tags)

        assert len(result) == 3
        assert all(tag in predefined_tags for tag in result)


class TestAudioLinkDetection:
    """audio link detection testing because multimedia matters"""

    def test_has_audio_links_basic(self):
        links = [
            "https://example.com/audio.mp3",
            "https://example.com/page.html"
        ]
        assert has_audio_links(links) is True

    def test_has_audio_links_multiple_formats(self):
        links = [
            "https://example.com/song.mp3",
            "https://example.com/podcast.wav",
            "https://example.com/music.ogg",
            "https://example.com/audio.flac"
        ]
        assert has_audio_links(links) is True

    def test_has_audio_links_with_query_params(self):
        links = [
            "https://example.com/audio.mp3?version=1&quality=high",
            "https://example.com/page.html"
        ]
        assert has_audio_links(links) is True

    def test_has_audio_links_case_insensitive(self):
        links = [
            "https://example.com/AUDIO.MP3",
            "https://example.com/song.WAV"
        ]
        assert has_audio_links(links) is True

    def test_has_audio_links_no_audio(self):
        links = [
            "https://example.com/page.html",
            "https://example.com/image.jpg",
            "https://example.com/document.pdf"
        ]
        assert has_audio_links(links) is False

    def test_has_audio_links_empty_list(self):
        assert has_audio_links([]) is False

    def test_has_audio_links_none_list(self):
        assert has_audio_links(None) is False

    def test_has_audio_links_none_elements(self):
        links = [None, "https://example.com/audio.mp3", None]
        assert has_audio_links(links) is True

    def test_has_audio_links_empty_strings(self):
        links = ["", "https://example.com/audio.mp3", ""]
        assert has_audio_links(links) is True

    def test_has_audio_links_false_positives(self):
        links = [
            "https://example.com/not_audio.mp3.html",  # mp3 in middle
            "https://example.com/mp3_in_name_but_not_extension.txt"
        ]
        assert has_audio_links(links) is False


class TestTextCleaning:
    """text cleaning testing because clean data is good data"""

    def test_clean_text_basic(self):
        text = "This is   a    test   with  multiple    spaces."
        result = clean_text(text)
        assert result == "This is a test with multiple spaces."

    def test_clean_text_empty(self):
        assert clean_text("") == ""

    def test_clean_text_none(self):
        assert clean_text(None) == ""

    def test_clean_text_whitespace_only(self):
        result = clean_text("   \t\n   ")
        assert result == ""

    def test_clean_text_special_characters_preserved(self):
        text = "Hello, world! How are you? (I'm fine.)"
        result = clean_text(text)
        assert "," in result
        assert "!" in result
        assert "?" in result
        assert "(" in result
        assert ")" in result

    def test_clean_text_special_characters_removed(self):
        text = "Text with @#$%^&*+=<>{}[]|\\`~ symbols"
        result = clean_text(text)
        # should remove these special characters
        unwanted = "@#$%^&*+=<>{}[]|\\`~"
        for char in unwanted:
            assert char not in result

    def test_clean_text_contractions_preserved(self):
        text = "Don't you think it's a great day?"
        result = clean_text(text)
        assert "Don't" in result
        assert "it's" in result

    def test_clean_text_newlines_and_tabs(self):
        text = "Line 1\nLine 2\tTabbed content\r\nWindows newline"
        result = clean_text(text)
        # should normalize to single spaces
        assert "\n" not in result
        assert "\t" not in result
        assert "\r" not in result

    def test_clean_text_unicode(self):
        text = "Café naïve résumé"
        result = clean_text(text)
        assert "Café" in result
        assert "naïve" in result
        assert "résumé" in result

    def test_clean_text_numbers_preserved(self):
        text = "The year 2023 has 365 days."
        result = clean_text(text)
        assert "2023" in result
        assert "365" in result

    def test_clean_text_hyphens_preserved(self):
        text = "This is a well-known fact about twenty-first century."
        result = clean_text(text)
        assert "well-known" in result
        assert "twenty-first" in result


class TestSimpleKeywordExtraction:
    """simple keyword extraction testing because sometimes simple is better"""

    def test_extract_keywords_simple_basic(self):
        text = "university student research academic program"
        result = extract_keywords_simple(text, top_k=3)
        assert len(result) <= 3
        assert all(word in text.lower() for word in result)

    def test_extract_keywords_simple_empty_text(self):
        result = extract_keywords_simple("", top_k=5)
        assert result == []

    def test_extract_keywords_simple_none_text(self):
        result = extract_keywords_simple(None, top_k=5)
        assert result == []

    def test_extract_keywords_simple_with_stop_words(self):
        text = "the university and the student"
        stop_words = {"the", "and"}
        result = extract_keywords_simple(text, stop_words=stop_words)

        assert "the" not in result
        assert "and" not in result
        assert "university" in result
        assert "student" in result

    def test_extract_keywords_simple_frequency_ranking(self):
        text = "university university student student student research"
        result = extract_keywords_simple(text, top_k=3)

        # "student" appears 3 times, "university" 2 times, "research" 1 time
        assert result[0] == "student"  # most frequent first

    def test_extract_keywords_simple_minimum_length(self):
        text = "a an to university research"
        result = extract_keywords_simple(text)

        # should only include words with 3+ characters
        assert "a" not in result
        assert "an" not in result
        assert "to" not in result
        assert "university" in result

    def test_extract_keywords_simple_case_insensitive(self):
        text = "University RESEARCH Student"
        result = extract_keywords_simple(text)

        # should normalize to lowercase
        assert all(word.islower() for word in result)

    def test_extract_keywords_simple_contractions(self):
        text = "don't can't won't university"
        result = extract_keywords_simple(text)

        # should handle contractions as single words
        assert "don't" in result or "dont" in result
        assert "university" in result

    def test_extract_keywords_simple_top_k_limit(self):
        text = " ".join([f"word{i}" for i in range(20)])
        result = extract_keywords_simple(text, top_k=5)

        assert len(result) == 5

    def test_extract_keywords_simple_no_stop_words(self):
        text = "the university and the student"
        result = extract_keywords_simple(text, stop_words=None)

        # without stop words, should include everything
        assert "the" in result
        assert "and" in result


class TestTextStatistics:
    """text statistics testing because numbers don't lie"""

    def test_get_text_stats_basic(self):
        text = "Hello world. This is a test."
        stats = get_text_stats(text)

        assert stats["word_count"] == 6
        assert stats["char_count"] == len(text)
        assert stats["sentence_count"] == 2
        assert stats["avg_word_length"] > 0

    def test_get_text_stats_empty_text(self):
        stats = get_text_stats("")

        assert stats["word_count"] == 0
        assert stats["char_count"] == 0
        assert stats["sentence_count"] == 0
        assert stats["avg_word_length"] == 0

    def test_get_text_stats_none_text(self):
        stats = get_text_stats(None)

        assert stats["word_count"] == 0
        assert stats["char_count"] == 0
        assert stats["sentence_count"] == 0
        assert stats["avg_word_length"] == 0

    def test_get_text_stats_single_word(self):
        text = "university"
        stats = get_text_stats(text)

        assert stats["word_count"] == 1
        assert stats["sentence_count"] == 0  # no sentence terminators
        assert stats["avg_word_length"] == len("university")

    def test_get_text_stats_contractions(self):
        text = "Don't you think it's great?"
        stats = get_text_stats(text)

        # contractions should count as single words
        assert stats["word_count"] == 5  # Don't, you, think, it's, great

    def test_get_text_stats_multiple_sentence_endings(self):
        text = "Question? Answer! Statement."
        stats = get_text_stats(text)

        assert stats["sentence_count"] == 3

    def test_get_text_stats_no_alpha_characters(self):
        text = "123 456 789"
        stats = get_text_stats(text)

        assert stats["word_count"] == 0  # only alphabetic tokens count
        assert stats["char_count"] == len(text)

    def test_get_text_stats_mixed_content(self):
        text = "Test 123 more text! Another sentence? Final."
        stats = get_text_stats(text)

        # should count only alphabetic words
        expected_words = ["Test", "more", "text", "Another", "sentence", "Final"]
        assert stats["word_count"] == len(expected_words)
        assert stats["sentence_count"] == 3

    def test_get_text_stats_unicode(self):
        text = "Café naïve résumé."
        stats = get_text_stats(text)

        assert stats["word_count"] == 3
        assert stats["char_count"] == len(text)

    def test_get_text_stats_average_calculation(self):
        text = "a bb ccc"  # words of length 1, 2, 3
        stats = get_text_stats(text)

        assert stats["word_count"] == 3
        assert stats["avg_word_length"] == 2.0  # (1+2+3)/3

    def test_get_text_stats_sentence_boundary_edge_cases(self):
        text = "Dr. Smith went to U.S.A. Really? Yes!"
        stats = get_text_stats(text)

        # should handle abbreviations appropriately
        assert stats["sentence_count"] >= 1


class TestErrorHandling:
    """error handling testing because robustness matters"""

    def test_nlp_functions_with_malformed_input(self):
        # test various functions with problematic input
        malformed_inputs = [
            None,
            "",
            "   ",
            "\0\x01\x02",  # control characters
            "🎉🎊🎈",  # emojis only
        ]

        for input_text in malformed_inputs:
            # should not crash
            try:
                clean_text(input_text)
                get_text_stats(input_text)
                extract_keywords_simple(input_text)
                extract_entities_and_keywords(input_text)
            except Exception as e:
                pytest.fail(f"Function crashed with input {repr(input_text)}: {e}")

    def test_memory_handling_large_text(self):
        # test with very large text
        large_text = "word " * 100000

        # should handle without memory issues
        stats = get_text_stats(large_text)
        assert stats["word_count"] == 100000

        keywords = extract_keywords_simple(large_text, top_k=10)
        assert len(keywords) <= 10

    def test_unicode_edge_cases(self):
        unicode_tests = [
            "🌟✨💫",  # emojis
            "مرحبا بالعالم",  # Arabic
            "こんにちは世界",  # Japanese
            "Ω≈ç√∫˜µ≤≥÷",  # mathematical symbols
            "нормальный текст",  # Cyrillic
        ]

        for text in unicode_tests:
            # should handle gracefully
            try:
                clean_text(text)
                get_text_stats(text)
                extract_keywords_simple(text)
            except Exception as e:
                pytest.fail(f"Unicode handling failed for {repr(text)}: {e}")

    def test_deeply_nested_structures(self):
        # test functions that might have recursion limits
        deeply_nested_text = "word " * 10000

        # should not hit recursion limits
        result = clean_text(deeply_nested_text)
        assert isinstance(result, str)

    def test_thread_safety_considerations(self):
        # basic thread safety test (not comprehensive)
        import threading
        import time

        results = []

        def worker():
            for i in range(10):
                result = extract_keywords_simple(f"word{i} " * 100)
                results.append(result)
                time.sleep(0.001)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # should complete without errors
        assert len(results) == 50