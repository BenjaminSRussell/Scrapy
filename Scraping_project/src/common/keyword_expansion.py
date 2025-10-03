"""
This module provides functionality for expanding keywords with synonyms.
"""
import logging

import nltk
from nltk.corpus import wordnet

logger = logging.getLogger(__name__)

def download_wordnet():
    """Downloads the WordNet corpus if not already downloaded."""
    try:
        nltk.data.find('corpora/wordnet.zip')
    except nltk.downloader.DownloadError:
        logger.info("Downloading WordNet corpus...")
        nltk.download('wordnet')

download_wordnet()

def expand_keywords(keywords: list[str]) -> dict[str, list[str]]:
    """
    Expands a list of keywords with their synonyms.

    Args:
        keywords: A list of keywords to expand.

    Returns:
        A dictionary where keys are the original keywords and values are lists
        of synonyms.
    """
    if not keywords:
        return {}

    expanded_keywords = {}
    for keyword in keywords:
        synonyms = set()
        for syn in wordnet.synsets(keyword):
            for lemma in syn.lemmas():
                synonyms.add(lemma.name().replace('_', ' '))
        if synonyms:
            expanded_keywords[keyword] = list(synonyms)

    logger.info(f"Expanded {len(expanded_keywords)} keywords.")
    return expanded_keywords
