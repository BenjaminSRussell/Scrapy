"""Simple tests for NLP functionality that actually work."""

import pytest
from common.nlp import (
    extract_entities_and_keywords,
    extract_content_tags,
    has_audio_links,
    clean_text,
    calculate_content_quality_score,
    detect_academic_relevance,
    identify_content_type
)


def test_extract_entities_and_keywords_works():
    """Test that entity/keyword extraction doesn't crash."""
    text = "University of Connecticut offers great research programs."

    entities, keywords = extract_entities_and_keywords(text)

    # Should return lists without crashing
    assert isinstance(entities, list)
    assert isinstance(keywords, list)


def test_content_tags_extraction():
    """Test URL path content tag extraction."""
    url_path = "/academics/courses/engineering"
    predefined_tags = {"academics", "courses", "engineering", "research"}

    tags = extract_content_tags(url_path, predefined_tags)

    assert "academics" in tags
    assert "courses" in tags
    assert "engineering" in tags


def test_audio_link_detection():
    """Test audio file detection."""
    links = [
        "https://example.com/lecture.mp3",
        "https://example.com/video.mp4",
        "https://example.com/page.html"
    ]

    assert has_audio_links(links) is True
    assert has_audio_links(["https://example.com/page.html"]) is False
    assert has_audio_links([]) is False


def test_text_cleaning():
    """Test text cleaning function."""
    messy_text = "This   has    weird\n\nspacing  and  stuff!!! @#$"
    cleaned = clean_text(messy_text)

    assert "weird spacing" in cleaned
    assert "!!!" in cleaned  # Should preserve some punctuation
    assert "@#$" not in cleaned  # Should remove weird chars


def test_content_quality_scoring():
    """Test content quality assessment."""
    # High quality content
    good_text = """
    This is a comprehensive article about university research programs.
    It covers various academic disciplines and provides detailed information
    about faculty research, course offerings, and degree requirements.
    The content includes information about admissions, academic excellence,
    and scholarly publications.
    """

    quality_score = calculate_content_quality_score(good_text, "Research Programs")
    assert 0.0 <= quality_score <= 1.0
    assert quality_score > 0.3  # Should be decent quality

    # Poor quality content
    bad_text = "Short text."
    poor_score = calculate_content_quality_score(bad_text, "")
    assert poor_score < quality_score


def test_academic_relevance_detection():
    """Test academic content detection."""
    academic_text = """
    The Department of Computer Science offers undergraduate and graduate
    degree programs. Faculty conduct research in areas including artificial
    intelligence, machine learning, and software engineering. Students can
    pursue coursework in algorithms, data structures, and programming languages.
    """

    relevance = detect_academic_relevance(academic_text)
    assert 0.0 <= relevance <= 1.0
    assert relevance > 0.3  # Should detect academic content

    non_academic_text = "This is just random text about nothing important."
    non_relevance = detect_academic_relevance(non_academic_text)
    assert non_relevance < relevance


def test_content_type_identification():
    """Test content type identification."""
    # URL-based detection
    assert identify_content_type("", "https://example.com/admissions/apply") == "admissions"
    assert identify_content_type("", "https://example.com/research/labs") == "research"
    assert identify_content_type("", "https://example.com/faculty/directory") == "faculty"

    # Content-based detection
    admissions_html = "<html><body>application deadline requirements apply now</body></html>"
    assert identify_content_type(admissions_html) == "admissions"

    research_html = "<html><body>research project laboratory publication</body></html>"
    assert identify_content_type(research_html) == "research"


def test_empty_input_handling():
    """Test that functions handle empty input gracefully."""
    assert extract_entities_and_keywords("") == ([], [])
    assert extract_content_tags("", set()) == []
    assert has_audio_links([]) is False
    assert clean_text("") == ""
    assert calculate_content_quality_score("") == 0.0
    assert detect_academic_relevance("") == 0.0
    assert identify_content_type("") == "unknown"