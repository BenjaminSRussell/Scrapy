"""Recency, Aggregation, and MetadataExtraction pipelines."""
import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from confluent_kafka import Producer as KafkaProducer
else:
    KafkaProducer = Any

try:
    from confluent_kafka import Producer

    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False
    Producer = None

try:
    from pydantic import ValidationError

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    ValidationError = Exception  # type: ignore

from itemadapter import ItemAdapter
from scrapy import Spider, signals
from scrapy.crawler import Crawler
from scrapy.exceptions import DropItem, NotConfigured

from src.items import OffsiteCandidateItem

logger = logging.getLogger(__name__)

from src.queue_routing import is_queue_routing_item  # noqa: E402  # re-export for #608

class RecencyScoringPipeline:

    def __init__(
        self,
        decay_constant: float = 0.01,
        default_score: float = 0.5,
    ):
        """Initialize the recency scoring pipeline.

        Args:
            decay_constant: Decay rate parameter (k). Higher = faster decay.
            default_score: Score for items missing publication_date
        """
        self.decay_constant = decay_constant
        self.default_score = default_score
        self.items_scored = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "RecencyScoringPipeline":
        decay_constant = crawler.settings.getfloat("RECENCY_DECAY_CONSTANT", 0.01)
        default_score = crawler.settings.getfloat("RECENCY_DEFAULT_SCORE", 0.5)

        return cls(
            decay_constant=decay_constant,
            default_score=default_score,
        )

    def process_item(self, item: Any, spider: Spider) -> Any:
        if isinstance(item, OffsiteCandidateItem):
            return item

        adapter = ItemAdapter(item)

        publication_date = adapter.get("publication_date")

        if publication_date:
            try:
                from src.common.scoring_metrics import calculate_decay_score

                score = calculate_decay_score(
                    publication_date=publication_date,
                    decay_constant=self.decay_constant,
                )
                adapter["recency_score"] = score
            except Exception as e:
                logger.warning(f"Failed to calculate recency score for {adapter.get('url')}: {e}")
                adapter["recency_score"] = self.default_score
        else:
            adapter["recency_score"] = self.default_score

        self.items_scored += 1

        if self.items_scored % 1000 == 0:
            logger.info(f"RecencyScoring: Scored {self.items_scored} items")

        return item

class AggregationPipeline:

    def __init__(
        self,
        enabled: bool = True,
        output_topic: str = "entity_summaries",
    ):
        """Initialize the aggregation pipeline.

        Args:
            enabled: Whether aggregation is enabled
            output_topic: Kafka topic for entity summaries
        """
        self.enabled = enabled
        self.output_topic = output_topic
        self.entity_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.items_aggregated = 0

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> "AggregationPipeline":
        enabled = crawler.settings.getbool("AGGREGATION_ENABLED", True)
        output_topic = crawler.settings.get("AGGREGATION_OUTPUT_TOPIC", "entity_summaries")

        pipeline = cls(enabled=enabled, output_topic=output_topic)

        crawler.signals.connect(pipeline.close_spider, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not self.enabled:
            return item

        if isinstance(item, OffsiteCandidateItem):
            return item

        adapter = ItemAdapter(item)
        entity_id = adapter.get("entity_id")

        if entity_id:
            item_dict = adapter.asdict()
            self.entity_groups[entity_id].append(item_dict)
            self.items_aggregated += 1

        return item

    def close_spider(self, spider: Spider) -> None:
        if not self.enabled:
            return

        logger.info(f"Closing AggregationPipeline for spider: {spider.name}")
        logger.info(f"Aggregated {self.items_aggregated} items into {len(self.entity_groups)} entity groups")

        for entity_id, items in self.entity_groups.items():
            items.sort(key=lambda x: x.get("recency_score", 0.0), reverse=True)

            summary = self._generate_entity_summary(entity_id, items)

            if summary:
                logger.info(f"Entity {entity_id}: Generated summary from {len(items)} items")
                logger.debug(f"Summary: {summary[:200]}...")

    def _generate_entity_summary(self, entity_id: str, items: list[dict[str, Any]]) -> str:

        context_parts = []
        for item in items[:10]:
            recency = item.get("recency_score", 0.0)
            title = item.get("title", "")
            content = item.get("content", "")[:200]
            context_parts.append(f"[Recency: {recency:.2f}] {title}: {content}")

        context = "\n".join(context_parts)

        _ = f"""Synthesize the following information about entity '{entity_id}'.
Prioritize facts from entries with higher recency scores (closer to 1.0).

{context}

Summary:"""

        return f"Summary for {entity_id} based on {len(items)} sources (most recent first)"

class MetadataExtractionPipeline:

    BATCH_SIZE = 100
    MAX_KEYWORDS = 10

    def __init__(
        self,
        enabled: bool = True,
        extractor_type: str = "yake",
        batch_size: int = 100,
        max_keywords: int = 10,
    ):
        """Initialize the metadata extraction pipeline.

        Args:
            enabled: Whether pipeline is enabled
            extractor_type: Type of keyword extractor ('yake' or 'spacy')
            batch_size: Number of items to batch before writing
            max_keywords: Maximum keywords to extract per document
        """
        self.enabled = enabled
        self.extractor_type = extractor_type
        self.batch_size = batch_size
        self.max_keywords = max_keywords
        self.batch = []
        self.items_processed = 0

        self.extractor = self._init_extractor(extractor_type)

    def _init_extractor(self, extractor_type: str):
        if extractor_type == "yake":
            try:
                import yake

                return yake.KeywordExtractor(
                    lan="en",
                    n=3,
                    dedupLim=0.9,
                    top=self.max_keywords,
                    features=None,
                )
            except ImportError:
                logger.warning("YAKE not installed, falling back to simple extractor")
                return None
        elif extractor_type == "spacy":
            try:
                import spacy

                return spacy.load("en_core_web_sm")
            except (ImportError, OSError):
                logger.warning("spaCy not available, falling back to simple extractor")
                return None
        else:
            logger.warning(f"Unknown extractor type: {extractor_type}, using simple extractor")
            return None

    @classmethod
    def from_crawler(cls, crawler: "Crawler") -> "MetadataExtractionPipeline":
        enabled = crawler.settings.getbool("METADATA_EXTRACTION_ENABLED", True)
        extractor_type = crawler.settings.get("METADATA_EXTRACTOR_TYPE", "yake")
        batch_size = crawler.settings.getint("METADATA_BATCH_SIZE", 100)
        max_keywords = crawler.settings.getint("METADATA_MAX_KEYWORDS", 10)

        pipeline = cls(
            enabled=enabled,
            extractor_type=extractor_type,
            batch_size=batch_size,
            max_keywords=max_keywords,
        )

        crawler.signals.connect(pipeline.spider_closed, signal=signals.spider_closed)

        return pipeline

    def process_item(self, item: Any, spider: Spider) -> Any:
        if not self.enabled:
            return item

        adapter = ItemAdapter(item)
        text_content = adapter.get("content") or adapter.get("text") or adapter.get("body")

        if not text_content or not isinstance(text_content, str):
            return item

        metadata = self._extract_metadata(text_content, adapter)

        adapter["extracted_metadata"] = metadata

        record = {
            "url": adapter.get("url"),
            "title": adapter.get("title", ""),
            "keywords": metadata.get("keywords", []),
            "entities": metadata.get("entities", {}),
            "extraction_timestamp": datetime.utcnow().isoformat() + "Z",
            "spider_name": spider.name,
        }

        self.batch.append(record)
        self.items_processed += 1

        if len(self.batch) >= self.batch_size:
            self._save_batch()

        if self.items_processed % 500 == 0:
            logger.info(
                f"[METADATA] Processed {self.items_processed} items, extracted metadata from {len(self.batch)} pending"
            )

        return item

    def _extract_metadata(self, text: str, adapter: ItemAdapter) -> dict[str, Any]:
        metadata = {"keywords": [], "entities": {}}

        if self.extractor:
            if self.extractor_type == "yake":
                keywords = self._extract_keywords_yake(text)
            elif self.extractor_type == "spacy":
                keywords, entities = self._extract_keywords_spacy(text)
                metadata["entities"] = entities
            else:
                keywords = self._extract_keywords_simple(text)
        else:
            keywords = self._extract_keywords_simple(text)

        metadata["keywords"] = keywords

        return metadata

    def _extract_keywords_yake(self, text: str) -> list[str]:
        try:
            keywords_with_scores = self.extractor.extract_keywords(text)
            return [kw for kw, score in keywords_with_scores[: self.max_keywords]]
        except Exception as e:
            logger.warning(f"YAKE extraction failed: {e}")
            return self._extract_keywords_simple(text)

    def _extract_keywords_spacy(self, text: str) -> tuple[list[str], dict[str, list[str]]]:
        try:
            doc = self.extractor(text[:1000000])

            keywords = []
            for chunk in doc.noun_chunks:
                if len(keywords) < self.max_keywords:
                    keywords.append(chunk.text.lower())

            entities = defaultdict(list)
            for ent in doc.ents:
                entities[ent.label_].append(ent.text)

            return keywords, dict(entities)

        except Exception as e:
            logger.warning(f"spaCy extraction failed: {e}")
            return self._extract_keywords_simple(text), {}

    def _extract_keywords_simple(self, text: str) -> list[str]:
        from collections import Counter

        words = re.findall(r"\b[a-z]{4,}\b", text.lower())

        stop_words = {
            "this",
            "that",
            "with",
            "from",
            "have",
            "been",
            "were",
            "said",
            "will",
            "they",
            "their",
            "what",
            "about",
            "which",
            "when",
            "there",
            "than",
            "them",
            "these",
            "would",
            "could",
            "should",
        }

        filtered_words = [w for w in words if w not in stop_words]

        counter = Counter(filtered_words)
        top_keywords = [word for word, count in counter.most_common(self.max_keywords)]

        return top_keywords

    def _save_batch(self):
        if not self.batch:
            return

        batch_size = len(self.batch)

        try:
            from src.utils.delta import get_delta

            delta = get_delta()
            delta.write("metadata_queue", self.batch, mode="append")
            logger.info(f" Saved {batch_size} metadata records to metadata_queue")

            self.batch.clear()
        except Exception as e:
            logger.error(f"Failed to save metadata batch: {e}")

    def spider_closed(self, spider: Spider) -> None:
        logger.info(f"[METADATA] Closing MetadataExtractionPipeline for spider: {spider.name}")

        if self.batch:
            self._save_batch()

        logger.info(f"[METADATA] Pipeline stats - Total processed: {self.items_processed}")
