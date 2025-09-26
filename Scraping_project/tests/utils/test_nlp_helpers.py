"""Tests for NLP helper utilities using sample-driven stubs."""

from __future__ import annotations

from collections import Counter

import pytest

import common.nlp as nlp_module


class FakeToken:
    def __init__(self, text: str):
        cleaned = text.strip(".,!?")
        self.text = text
        self.lemma_ = cleaned.lower()
        self.is_alpha = cleaned.isalpha()


class FakeDoc:
    def __init__(self, text: str):
        from types import SimpleNamespace

        self.text = text
        self.ents = []
        if "UConn" in text:
            self.ents.append(SimpleNamespace(text="UConn", label_="ORG"))
        self._tokens = [FakeToken(token) for token in text.split()]

    def __iter__(self):
        return iter(self._tokens)


class StubRegistry:
    def __init__(self):
        self.stop_words = {"the", "and", "is", "a", "for"}
        self.entity_labels = {"ORG"}

    def extract_with_spacy(self, text: str, top_k: int):
        doc = FakeDoc(text)
        entities = []
        seen = set()
        for ent in doc.ents:
            name = ent.text.strip()
            if name and name not in seen:
                entities.append(name)
                seen.add(name)

        keywords_source = [
            token.lemma_.lower()
            for token in doc
            if token.is_alpha and token.lemma_.lower() not in self.stop_words
        ]
        keywords = [word for word, _ in Counter(keywords_source).most_common(top_k)]
        return entities, keywords

    def extract_entities_with_transformer(self, text: str):
        doc = FakeDoc(text)
        return [ent.text.strip() for ent in doc.ents]


@pytest.fixture
def stub_registry(monkeypatch):
    registry = StubRegistry()
    monkeypatch.setattr(nlp_module, "get_registry", lambda: registry)
    return registry


@pytest.mark.parametrize(
    "text,expected_entities",
    [
        ("UConn offers innovative programs for students", ["UConn"]),
        ("The research initiatives and data science labs are growing", []),
    ],
)
def test_extract_entities_and_keywords_spacy(stub_registry, text, expected_entities):
    entities, keywords = nlp_module.extract_entities_and_keywords(text, top_k=10)
    assert entities == expected_entities
    assert len(keywords) <= 10


def test_extract_entities_and_keywords_transformer(stub_registry):
    text = "UConn students explore AI labs"
    entities, keywords = nlp_module.extract_entities_and_keywords(
        text, backend="transformer"
    )
    assert entities == ["UConn"]
    assert keywords


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
