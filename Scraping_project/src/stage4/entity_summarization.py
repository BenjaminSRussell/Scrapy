import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

class FactAggregator:

    def __init__(
        self,
        embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        similarity_threshold: float = 0.85,
        min_fact_length: int = 20,
        max_fact_length: int = 500,
    ):
        """Initialize the fact aggregator.

        Args:
            embedding_model_name: HuggingFace sentence embedding model
            similarity_threshold: Cosine similarity threshold for deduplication (0-1)
            min_fact_length: Minimum character length for a valid fact
            max_fact_length: Maximum character length for a fact
        """
        self.embedding_model_name = embedding_model_name
        self.similarity_threshold = similarity_threshold
        self.min_fact_length = min_fact_length
        self.max_fact_length = max_fact_length

        self._embedding_model = None

        self.entity_facts: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def _get_embedding_model(self):
        if self._embedding_model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info(f"Loading embedding model: {self.embedding_model_name}")
                self._embedding_model = SentenceTransformer(self.embedding_model_name)
                logger.info("✅ Embedding model loaded successfully")
            except ImportError as e:
                raise ImportError(
                    "sentence-transformers is required for FactAggregator. "
                    "Install it with: pip install sentence-transformers"
                ) from e

        return self._embedding_model

    def add_document(
        self,
        entity_name: str,
        entity_type: str,
        content: str,
        source_url: str,
        publication_date: datetime | str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Add a document's content as facts for an entity.

        This method extracts sentences from the document content and associates them
        with the specified entity, preserving source attribution.

        Args:
            entity_name: Name of the entity (e.g., "Professor Jane Doe")
            entity_type: Type of entity (e.g., "person", "organization")
            content: Text content to extract facts from
            source_url: URL of the source document
            publication_date: Date of publication (datetime or ISO 8601 string)
            metadata: Optional additional metadata
        """
        pub_date = self._parse_date(publication_date)

        sentences = self._extract_sentences(content)

        entity_sentences = self._filter_entity_sentences(sentences, entity_name)

        for sentence in entity_sentences:
            fact_record = {
                "entity_name": entity_name,
                "entity_type": entity_type,
                "fact_text": sentence,
                "source_url": source_url,
                "publication_date": pub_date,
                "metadata": metadata or {},
                "timestamp_added": datetime.utcnow(),
            }

            self.entity_facts[entity_name].append(fact_record)

        logger.info(f"Added {len(entity_sentences)} facts for entity '{entity_name}' from {source_url}")

    def _parse_date(self, date_input: datetime | str | None) -> datetime | None:
        if date_input is None:
            return None

        if isinstance(date_input, datetime):
            return date_input

        if isinstance(date_input, str):
            try:
                if date_input.endswith("Z"):
                    date_input = date_input[:-1] + "+00:00"

                return datetime.fromisoformat(date_input)
            except ValueError:
                logger.warning(f"Failed to parse date: {date_input}")
                return None

        return None

    def _extract_sentences(self, content: str) -> list[str]:
        sentence_pattern = re.compile(r"[^.!?]+[.!?]+")
        sentences = sentence_pattern.findall(content)

        cleaned = []
        for sent in sentences:
            sent = sent.strip()

            if self.min_fact_length <= len(sent) <= self.max_fact_length:
                cleaned.append(sent)

        return cleaned

    def _filter_entity_sentences(self, sentences: list[str], entity_name: str) -> list[str]:
        entity_terms = entity_name.lower().split()

        filtered = []
        for sent in sentences:
            sent_lower = sent.lower()

            if any(term in sent_lower for term in entity_terms):
                filtered.append(sent)

        return filtered

    def deduplicate_facts(self, entity_name: str) -> list[dict[str, Any]]:
        facts = self.entity_facts.get(entity_name, [])

        if not facts:
            logger.warning(f"No facts found for entity: {entity_name}")
            return []

        if len(facts) == 1:
            return facts

        logger.info(f"Deduplicating {len(facts)} facts for '{entity_name}'...")

        fact_texts = [f["fact_text"] for f in facts]

        model = self._get_embedding_model()
        embeddings = model.encode(fact_texts, convert_to_numpy=True)

        similarity_matrix = self._compute_similarity_matrix(embeddings)

        clusters = self._cluster_facts(similarity_matrix)

        deduplicated = []
        for cluster_indices in clusters:
            cluster_facts = [facts[i] for i in cluster_indices]
            representative = self._select_representative_fact(cluster_facts)

            source_refs = []
            for fact in cluster_facts:
                source_refs.append(
                    {
                        "source_url": fact["source_url"],
                        "publication_date": fact["publication_date"],
                    }
                )

            representative["source_references"] = source_refs
            deduplicated.append(representative)

        logger.info(f"✅ Deduplicated {len(facts)} facts → {len(deduplicated)} unique facts for '{entity_name}'")

        return deduplicated

    def _compute_similarity_matrix(self, embeddings: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / (norms + 1e-10)

        similarity = np.dot(normalized, normalized.T)

        return similarity

    def _cluster_facts(self, similarity_matrix: np.ndarray) -> list[list[int]]:
        n = similarity_matrix.shape[0]
        clustered = set()
        clusters = []

        for i in range(n):
            if i in clustered:
                continue

            similar_indices = []
            for j in range(n):
                if similarity_matrix[i, j] >= self.similarity_threshold:
                    similar_indices.append(j)
                    clustered.add(j)

            if similar_indices:
                clusters.append(similar_indices)

        return clusters

    def _select_representative_fact(self, cluster_facts: list[dict[str, Any]]) -> dict[str, Any]:
        sorted_facts = sorted(
            cluster_facts,
            key=lambda f: (
                f["publication_date"] or datetime.min,
                len(f["fact_text"]),
            ),
            reverse=True,
        )

        return sorted_facts[0]

    def get_all_deduplicated_facts(self) -> dict[str, list[dict[str, Any]]]:
        result = {}
        for entity_name in self.entity_facts.keys():
            result[entity_name] = self.deduplicate_facts(entity_name)

        return result

class ChronologicalSorter:

    def __init__(self, date_format: str = "%Y-%m-%d"):
        self.date_format = date_format

    def sort_facts(self, facts: list[dict[str, Any]], descending: bool = False) -> list[dict[str, Any]]:
        sorted_facts = sorted(facts, key=lambda f: f.get("publication_date") or datetime.min, reverse=descending)

        return sorted_facts

    def prepare_for_summarization(self, facts: list[dict[str, Any]]) -> str:
        sorted_facts = self.sort_facts(facts, descending=False)

        formatted_lines = []
        for fact in sorted_facts:
            pub_date = fact.get("publication_date")
            fact_text = fact.get("fact_text", "")

            if pub_date:
                date_str = pub_date.strftime(self.date_format)
                formatted_lines.append(f"({date_str}): {fact_text}")
            else:
                formatted_lines.append(f"(Date unknown): {fact_text}")

        formatted_text = "\n".join(formatted_lines)

        return formatted_text

class AbstractiveSummarizer:

    def __init__(
        self,
        model_name: str = "facebook/bart-large-cnn",
        max_length: int = 300,
        min_length: int = 100,
        device: int = -1,
    ):
        """Initialize the abstractive summarizer.

        Args:
            model_name: HuggingFace summarization model
            max_length: Maximum summary length in tokens
            min_length: Minimum summary length in tokens
            device: Device for inference (-1 for CPU, 0+ for GPU)
        """
        self.model_name = model_name
        self.max_length = max_length
        self.min_length = min_length
        self.device = device

        self._summarizer = None

    def _get_summarizer(self):
        if self._summarizer is None:
            try:
                from transformers import pipeline

                logger.info(f"Loading summarization model: {self.model_name}")
                self._summarizer = pipeline(
                    "summarization",
                    model=self.model_name,
                    device=self.device,
                )
                logger.info("✅ Summarization model loaded successfully")
            except ImportError as e:
                raise ImportError(
                    "transformers is required for AbstractiveSummarizer. "
                    "Install it with: pip install transformers torch"
                ) from e

        return self._summarizer

    def summarize(
        self,
        input_text: str,
        facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Generate abstractive summary with citations.

        Args:
            input_text: Chronologically-sorted facts with date prefixes
            facts: Original FactRecords for citation mapping

        Returns:
            Dictionary with 'summary_text' and 'citations' mapping
        """
        if not input_text.strip():
            logger.warning("Empty input text for summarization")
            return {
                "summary_text": "",
                "citations": {},
            }

        summarizer = self._get_summarizer()

        max_input_chars = 4000
        if len(input_text) > max_input_chars:
            input_text = input_text[:max_input_chars]
            logger.warning(f"Truncated input text to {max_input_chars} characters")

        try:
            result = summarizer(
                input_text,
                max_length=self.max_length,
                min_length=self.min_length,
                do_sample=False,
            )

            summary_text = result[0]["summary_text"]
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            summary_text = ". ".join(input_text.split(".")[:3]) + "."

        citations = self._create_citations(facts)

        summary_with_citations = self._embed_citations(summary_text, citations)

        return {
            "summary_text": summary_with_citations,
            "citations": citations,
        }

    def _create_citations(self, facts: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
        citations = {}
        citation_num = 1

        for fact in facts:
            source_refs = fact.get("source_references", [])
            if source_refs:
                citations[citation_num] = source_refs
                citation_num += 1

        return citations

    def _embed_citations(self, summary_text: str, citations: dict[int, list[dict[str, Any]]]) -> str:
        if not citations:
            return summary_text

        citation_markers = []
        for num in sorted(citations.keys()):
            source_refs = citations[num]
            if source_refs:
                ref = source_refs[0]
                _ = ref.get("source_url", "")
                citation_markers.append(f"[{num}]")

        if citation_markers:
            summary_with_citations = f"{summary_text} {' '.join(citation_markers)}"
        else:
            summary_with_citations = summary_text

        return summary_with_citations

class EntitySummaryStorage:

    def __init__(self, delta_manager=None):
        if delta_manager is None:
            from src.utils.delta import get_delta

            delta_manager = get_delta()

        self.delta = delta_manager
        self.table_name = "entity_summaries"

        self.schema = {
            "entity_name": "string",
            "entity_type": "string",
            "summary_text": "string",
            "source_references": "string",
            "last_updated": "string",
            "fact_count": "int",
            "created_at": "string",
        }

    def save_summary(
        self,
        entity_name: str,
        entity_type: str,
        summary_text: str,
        citations: dict[int, list[dict[str, Any]]],
        facts: list[dict[str, Any]],
    ):
        """Save entity summary to Delta Lake.

        Args:
            entity_name: Name of the entity
            entity_type: Type of entity (person, organization, etc.)
            summary_text: Generated summary with citations
            citations: Citation mapping
            facts: Original deduplicated facts
        """
        import json

        source_refs_map = {}
        for citation_num, refs in citations.items():
            source_refs_map[str(citation_num)] = [
                {
                    "url": ref.get("source_url", ""),
                    "date": ref.get("publication_date").isoformat() if ref.get("publication_date") else None,
                }
                for ref in refs
            ]

        all_dates = []
        for fact in facts:
            if fact.get("publication_date"):
                all_dates.append(fact["publication_date"])

        last_updated = max(all_dates).isoformat() if all_dates else datetime.utcnow().isoformat()

        record = {
            "entity_name": entity_name,
            "entity_type": entity_type,
            "summary_text": summary_text,
            "source_references": json.dumps(source_refs_map),
            "last_updated": last_updated,
            "fact_count": len(facts),
            "created_at": datetime.utcnow().isoformat(),
        }

        self.delta.write(self.table_name, [record], mode="append")

        logger.info(f"✅ Saved summary for '{entity_name}' to Delta Lake table '{self.table_name}'")

    def read_summaries(
        self,
        entity_name: str | None = None,
        entity_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read entity summaries from Delta Lake.

        Args:
            entity_name: Filter by entity name (optional)
            entity_type: Filter by entity type (optional)

        Returns:
            List of entity summary records
        """
        filters = []
        if entity_name:
            filters.append(f"entity_name = '{entity_name}'")
        if entity_type:
            filters.append(f"entity_type = '{entity_type}'")

        filter_expr = " AND ".join(filters) if filters else None

        records = self.delta.read(self.table_name, filters=filter_expr)

        return records

class Stage4EntityWorker:

    def __init__(
        self,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        summarization_model: str = "facebook/bart-large-cnn",
        similarity_threshold: float = 0.85,
        device: int = -1,
    ):
        """Initialize the Stage 4 worker.

        Args:
            embedding_model: Sentence embedding model for deduplication
            summarization_model: Abstractive summarization model
            similarity_threshold: Threshold for semantic deduplication
            device: Device for model inference (-1 for CPU)
        """
        self.fact_aggregator = FactAggregator(
            embedding_model_name=embedding_model,
            similarity_threshold=similarity_threshold,
        )

        self.chronological_sorter = ChronologicalSorter()

        self.summarizer = AbstractiveSummarizer(
            model_name=summarization_model,
            device=device,
        )

        self.storage = EntitySummaryStorage()

        logger.info("✅ Stage4EntityWorker initialized")

    def process_documents(self, documents: list[dict[str, Any]]):
        logger.info(f"Processing {len(documents)} documents...")

        for doc in documents:
            self.fact_aggregator.add_document(
                entity_name=doc["entity_name"],
                entity_type=doc["entity_type"],
                content=doc["content"],
                source_url=doc["source_url"],
                publication_date=doc.get("publication_date"),
                metadata=doc.get("metadata"),
            )

        all_entity_facts = self.fact_aggregator.get_all_deduplicated_facts()

        for entity_name, facts in all_entity_facts.items():
            if not facts:
                continue

            entity_type = facts[0].get("entity_type", "unknown")

            chronological_text = self.chronological_sorter.prepare_for_summarization(facts)

            summary_result = self.summarizer.summarize(chronological_text, facts)

            self.storage.save_summary(
                entity_name=entity_name,
                entity_type=entity_type,
                summary_text=summary_result["summary_text"],
                citations=summary_result["citations"],
                facts=facts,
            )

            logger.info(f"✅ Completed processing for entity '{entity_name}' ({len(facts)} unique facts)")

        logger.info(f"✅ Stage 4 processing complete. Processed {len(all_entity_facts)} entities.")

if __name__ == "__main__":
    sample_documents = [
        {
            "entity_name": "Professor Jane Doe",
            "entity_type": "person",
            "content": (
                "Professor Jane Doe joined UConn in 2020 as Associate Professor of Computer Science. "
                "She received the NSF CAREER Award in 2021 for her work on machine learning. "
                "In 2023, Professor Doe published a groundbreaking paper on neural networks in Nature. "
                "Her research focuses on deep learning and AI safety."
            ),
            "source_url": "https://uconn.edu/faculty/jane-doe",
            "publication_date": "2023-01-15",
        },
        {
            "entity_name": "Professor Jane Doe",
            "entity_type": "person",
            "content": (
                "Dr. Jane Doe was promoted to Full Professor in 2024. "
                "She now leads the AI Research Lab at UConn. "
                "Professor Doe received the NSF CAREER Award in 2021. "
                "Her lab has 15 PhD students working on various AI projects."
            ),
            "source_url": "https://uconn.edu/news/jane-doe-promotion",
            "publication_date": "2024-06-01",
        },
        {
            "entity_name": "UConn AI Lab",
            "entity_type": "organization",
            "content": (
                "The UConn AI Research Lab was established in 2022. "
                "It is directed by Professor Jane Doe. "
                "The lab has published over 50 papers in top-tier conferences. "
                "Current research areas include computer vision, NLP, and robotics."
            ),
            "source_url": "https://uconn.edu/research/ai-lab",
            "publication_date": "2024-03-10",
        },
    ]

    worker = Stage4EntityWorker()

    worker.process_documents(sample_documents)
