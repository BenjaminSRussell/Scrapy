import os, json, hashlib, itertools, time
from collections import Counter
from pathlib import Path
from string import punctuation

import spacy
from itemadapter import ItemAdapter

DATA_FILE = Path("uconn_data.jsonl")
STOP_CHARS = set(punctuation)
STOP_WORDS = set(spacy.lang.en.stop_words.STOP_WORDS)

class DedupAndWritePipeline:
    """Append‑only .jsonl with SHA‑1 de‑duplication + integrity stats."""

    def open_spider(self, spider):
        self.file = DATA_FILE.open("a", encoding="utf‑8")
        self.seen = set()
        self.bad_bodies = 0
        if DATA_FILE.exists():
            for line in DATA_FILE.open(encoding="utf‑8"):
                try:
                    self.seen.add(json.loads(line).get("url_hash"))
                except json.JSONDecodeError:
                    continue
        spider.logger.info(f"[PIPE] Loaded {len(self.seen):,} existing hashes")

    def close_spider(self, spider):
        self.file.close()

    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        h = adapter["url_hash"]
        if h in self.seen:
            return item

        # fail‑fast: body size 0‑byte guard
        if adapter["size_bytes"] == 0:
            self.bad_bodies += 1
            if self.bad_bodies > 50 and self.bad_bodies / (len(self.seen) + 1) > 0.02:
                spider.crawler.engine.close_spider(spider, reason="too_many_zero_bodies")
            return item

        self.file.write(json.dumps(dict(adapter), ensure_ascii=False) + "\n")
        self.seen.add(h)
        return item