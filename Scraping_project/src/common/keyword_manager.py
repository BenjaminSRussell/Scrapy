# src/common/keyword_manager.py
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class KeywordManager:
    _instance = None
    _keywords = None
    _keywords_path = Path(__file__).parent.parent / "config" / "keywords.txt"

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(KeywordManager, cls).__new__(cls)
            cls._instance._load_keywords()
        return cls._instance

    def _load_keywords(self):
        if self._keywords is None:
            try:
                with open(self._keywords_path, 'r', encoding='utf-8') as f:
                    self._keywords = {line.strip().lower() for line in f if line.strip()}
                logger.info(f"Loaded {len(self._keywords)} keywords from {self._keywords_path}")
            except FileNotFoundError:
                self._keywords = set()
                logger.warning(f"Keywords file not found: {self._keywords_path}")

    def get_keywords(self):
        return self._keywords
