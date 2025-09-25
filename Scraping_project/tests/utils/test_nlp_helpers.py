"""Tests for NLP helper utilities using sample-driven stubs."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import common.nlp as nlp_module


class FakeToken:
    def __init__(self, text: str):
        cleaned = text.strip(".,!?")
        self.lemma_ = cleaned.lower()
        self.is_alpha = cleaned.isalpha()
        self.is_stop = self.lemma_ in {"the", "and", "is", "a"}


class FakeDoc:
    def __init__(self, text: str):
        self.text = text
        self.ents = []
        if "UConn" in text:
            self.ents.append(SimpleNamespace(text="UConn", label_="ORG"))
        self._tokens = [FakeToken(token) for token in text.split()]

    def __iter__(self):
        return iter(self._tokens)


class FakeNLP:
    pipe_labels = {"ner": {"ORG"}}

    def __call__(self, text: str):
        return FakeDoc(text)


@pytest.fixture(autouse=True)
def reset_nlp_globals():
    nlp_module.NLP = None
    nlp_module.ENTITY_LABELS = None
    yield
    nlp_module.NLP = None
    nlp_module.ENTITY_LABELS = None


@pytest.fixture
def patched_spacy(monkeypatch):
    fake_spacy = SimpleNamespace(load=lambda model, disable=None: FakeNLP())
    monkeypatch.setitem(sys.modules, "spacy", fake_spacy)
    yield
    monkeypatch.delitem(sys.modules, "spacy", raising=False)


@pytest.mark.usefixtures("patched_spacy")
def test_load_nlp_model_initialises_stub():
    loaded = nlp_module.load_nlp_model()
    assert loaded is True
    assert nlp_module.NLP is not None
    assert "ORG" in nlp_module.ENTITY_LABELS


@pytest.mark.usefixtures("patched_spacy")
@pytest.mark.parametrize(
    "text,expected_entities,expected_keywords",
    [
        (
            "UConn offers innovative programs for students",
            ["UConn"],
            {"uconn", "offers", "innovative", "programs", "for", "students"},
        ),
        (
            "The research initiatives and data science labs are growing",
            [],
            {"research", "initiatives", "data", "science", "labs", "are", "growing"},
        ),
    ],
)
def test_extract_entities_and_keywords_stub(text, expected_entities, expected_keywords):
    entities, keywords = nlp_module.extract_entities_and_keywords(text, top_k=10)
    assert entities == expected_entities
    assert set(keywords).issubset(expected_keywords)


@pytest.mark.parametrize(
    "url_path,predefined,expected",
    [
        ("/admissions/undergraduate/apply", {"admissions", "apply"}, ["admissions", "apply"]),
        ("/research/ai/labs", {"ai", "labs"}, ["ai", "labs"]),
        ("/students/life", {"housing", "dining"}, []),
    ],
)
def test_extract_content_tags(url_path, predefined, expected):
    tags = nlp_module.extract_content_tags(url_path, predefined)
    assert tags == expected


@pytest.mark.parametrize(
    "links,expected",
    [
        (["https://uconn.edu/audio.mp3"], True),
        (["https://uconn.edu/video.mp4"], False),
        (["https://uconn.edu/resources/audio.wav", "https://uconn.edu/doc.pdf"], True),
        ([], False),
    ],
)
def test_has_audio_links(links, expected):
    assert nlp_module.has_audio_links(links) is expected


def test_nlp_fallback_without_spacy(monkeypatch):
    monkeypatch.setitem(sys.modules, "spacy", None)
    nlp_module.NLP = None
    loaded = nlp_module.load_nlp_model()
    assert loaded is False
    entities, keywords = nlp_module.extract_entities_and_keywords("Text without model")
    assert entities == []
    assert keywords == []
