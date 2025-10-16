"""Stage 4: Entity-Centric Summarization with Verifiable Citations

This module implements a sophisticated, entity-centric summarization pipeline that produces
verifiable, non-redundant, and chronologically-aware summaries from scraped content.

The pipeline consists of three main phases:
1. Extractive Fact Aggregation and Deduplication
2. Abstractive Summarization with Chronological Context
3. Structured Output and Storage

All NLP tasks use local Hugging Face models to ensure the system is self-contained.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class FactAggregator:
    """Aggregates and deduplicates facts by entity using semantic clustering.

    This class implements Phase 1 of the entity-centric summarization pipeline:
    - Aggregates facts (sentences) by entity across multiple source documents
    - Preserves source references (URL, date) for each fact
    - Implements semantic deduplication using sentence embeddings and clustering

    Features:
    - Sentence-level fact extraction with entity co-reference
    - Semantic deduplication via cosine similarity clustering
    - Source attribution for provenance tracking
    - Handles multiple date formats (ISO 8601, timestamps, etc.)
    """

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

        # Lazy-load the embedding model on first use
        self._embedding_model = None

        # Entity fact storage: {entity_name: [list of FactRecord]}
        self.entity_facts: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def _get_embedding_model(self):
        """Lazy-load the sentence embedding model."""
        if self._embedding_model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info(f"Loading embedding model: {self.embedding_model_name}")
                self._embedding_model = SentenceTransformer(self.embedding_model_name)
                logger.info("✅ Embedding model loaded successfully")
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for FactAggregator. "
                    "Install it with: pip install sentence-transformers"
                )

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
        # Parse publication date to datetime
        pub_date = self._parse_date(publication_date)

        # Extract sentences from content
        sentences = self._extract_sentences(content)

        # Filter sentences that mention the entity
        entity_sentences = self._filter_entity_sentences(sentences, entity_name)

        # Create FactRecords
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

        logger.info(
            f"Added {len(entity_sentences)} facts for entity '{entity_name}' "
            f"from {source_url}"
        )

    def _parse_date(self, date_input: datetime | str | None) -> datetime | None:
        """Parse various date formats to datetime object.

        Args:
            date_input: Date in various formats

        Returns:
            Parsed datetime or None if parsing fails
        """
        if date_input is None:
            return None

        if isinstance(date_input, datetime):
            return date_input

        if isinstance(date_input, str):
            try:
                # Handle ISO 8601 format with 'Z' suffix
                if date_input.endswith("Z"):
                    date_input = date_input[:-1] + "+00:00"

                return datetime.fromisoformat(date_input)
            except ValueError:
                logger.warning(f"Failed to parse date: {date_input}")
                return None

        return None

    def _extract_sentences(self, content: str) -> list[str]:
        """Extract well-formed sentences from content.

        Uses simple sentence boundary detection. For production, consider
        using spaCy or nltk for more sophisticated sentence splitting.

        Args:
            content: Text content

        Returns:
            List of sentences
        """
        # Simple sentence splitting (handles ., !, ?)
        # For production, use spaCy: nlp(content).sents
        sentence_pattern = re.compile(r'[^.!?]+[.!?]+')
        sentences = sentence_pattern.findall(content)

        # Clean and filter sentences
        cleaned = []
        for sent in sentences:
            sent = sent.strip()

            # Filter by length
            if self.min_fact_length <= len(sent) <= self.max_fact_length:
                cleaned.append(sent)

        return cleaned

    def _filter_entity_sentences(
        self,
        sentences: list[str],
        entity_name: str
    ) -> list[str]:
        """Filter sentences that mention the entity.

        This is a simple keyword-based filter. For production, consider using
        named entity recognition (NER) and co-reference resolution.

        Args:
            sentences: List of sentences
            entity_name: Entity name to search for

        Returns:
            Sentences mentioning the entity
        """
        # Extract key terms from entity name (e.g., "Jane Doe" → ["Jane", "Doe"])
        entity_terms = entity_name.lower().split()

        filtered = []
        for sent in sentences:
            sent_lower = sent.lower()

            # Check if any entity term appears in sentence
            if any(term in sent_lower for term in entity_terms):
                filtered.append(sent)

        return filtered

    def deduplicate_facts(self, entity_name: str) -> list[dict[str, Any]]:
        """Deduplicate facts for an entity using semantic clustering.

        This method uses sentence embeddings and cosine similarity to identify
        semantically similar facts. From each cluster of similar facts, it selects
        the most representative one (e.g., longest, most recent).

        Args:
            entity_name: Entity to deduplicate facts for

        Returns:
            Deduplicated list of FactRecords with source references
        """
        facts = self.entity_facts.get(entity_name, [])

        if not facts:
            logger.warning(f"No facts found for entity: {entity_name}")
            return []

        if len(facts) == 1:
            return facts

        logger.info(f"Deduplicating {len(facts)} facts for '{entity_name}'...")

        # Extract fact texts
        fact_texts = [f["fact_text"] for f in facts]

        # Generate embeddings
        model = self._get_embedding_model()
        embeddings = model.encode(fact_texts, convert_to_numpy=True)

        # Compute pairwise cosine similarity matrix
        similarity_matrix = self._compute_similarity_matrix(embeddings)

        # Cluster similar facts
        clusters = self._cluster_facts(similarity_matrix)

        # Select representative fact from each cluster
        deduplicated = []
        for cluster_indices in clusters:
            cluster_facts = [facts[i] for i in cluster_indices]
            representative = self._select_representative_fact(cluster_facts)

            # Aggregate all source references for this cluster
            source_refs = []
            for fact in cluster_facts:
                source_refs.append({
                    "source_url": fact["source_url"],
                    "publication_date": fact["publication_date"],
                })

            # Add aggregated sources to representative fact
            representative["source_references"] = source_refs
            deduplicated.append(representative)

        logger.info(
            f"✅ Deduplicated {len(facts)} facts → {len(deduplicated)} unique facts "
            f"for '{entity_name}'"
        )

        return deduplicated

    def _compute_similarity_matrix(
        self,
        embeddings: np.ndarray
    ) -> np.ndarray:
        """Compute pairwise cosine similarity matrix.

        Args:
            embeddings: Sentence embeddings (n_samples, embedding_dim)

        Returns:
            Similarity matrix (n_samples, n_samples)
        """
        # Normalize embeddings for cosine similarity
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / (norms + 1e-10)  # Avoid division by zero

        # Compute cosine similarity: dot product of normalized vectors
        similarity = np.dot(normalized, normalized.T)

        return similarity

    def _cluster_facts(self, similarity_matrix: np.ndarray) -> list[list[int]]:
        """Cluster facts using greedy clustering based on similarity threshold.

        This implements a simple greedy clustering algorithm:
        1. Start with first unclustered fact
        2. Find all facts similar to it (above threshold)
        3. Create a cluster with these facts
        4. Mark them as clustered
        5. Repeat until all facts are clustered

        For production, consider using community detection algorithms like
        Leiden or Louvain for better clustering quality.

        Args:
            similarity_matrix: Pairwise similarity matrix

        Returns:
            List of clusters, where each cluster is a list of fact indices
        """
        n = similarity_matrix.shape[0]
        clustered = set()
        clusters = []

        for i in range(n):
            if i in clustered:
                continue

            # Find all facts similar to fact i
            similar_indices = []
            for j in range(n):
                if similarity_matrix[i, j] >= self.similarity_threshold:
                    similar_indices.append(j)
                    clustered.add(j)

            if similar_indices:
                clusters.append(similar_indices)

        return clusters

    def _select_representative_fact(
        self,
        cluster_facts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Select the most representative fact from a cluster.

        Selection criteria (in order of priority):
        1. Most recent publication date
        2. Longest fact text (more detailed)

        Args:
            cluster_facts: Facts in the cluster

        Returns:
            Representative FactRecord
        """
        # Sort by publication date (most recent first), then by length (longest first)
        sorted_facts = sorted(
            cluster_facts,
            key=lambda f: (
                f["publication_date"] or datetime.min,  # Handle None dates
                len(f["fact_text"])
            ),
            reverse=True
        )

        return sorted_facts[0]

    def get_all_deduplicated_facts(self) -> dict[str, list[dict[str, Any]]]:
        """Get deduplicated facts for all entities.

        Returns:
            Dictionary mapping entity names to deduplicated facts
        """
        result = {}
        for entity_name in self.entity_facts.keys():
            result[entity_name] = self.deduplicate_facts(entity_name)

        return result


class ChronologicalSorter:
    """Sorts facts chronologically and prepares them for summarization.

    This class implements Phase 2a of the pipeline: chronological organization
    of facts with date-based context cues for the summarization model.
    """

    def __init__(self, date_format: str = "%Y-%m-%d"):
        """Initialize the chronological sorter.

        Args:
            date_format: Format string for date display
        """
        self.date_format = date_format

    def sort_facts(
        self,
        facts: list[dict[str, Any]],
        descending: bool = False
    ) -> list[dict[str, Any]]:
        """Sort facts by publication date.

        Args:
            facts: List of FactRecords
            descending: If True, sort newest first. If False, oldest first.

        Returns:
            Sorted facts
        """
        sorted_facts = sorted(
            facts,
            key=lambda f: f.get("publication_date") or datetime.min,
            reverse=descending
        )

        return sorted_facts

    def prepare_for_summarization(
        self,
        facts: list[dict[str, Any]]
    ) -> str:
        """Prepare facts for summarization with date context.

        This method creates a text input for the summarization model by:
        1. Sorting facts chronologically (oldest to newest)
        2. Prepending each fact with its publication date
        3. Concatenating all facts with proper formatting

        Args:
            facts: List of FactRecords

        Returns:
            Formatted text ready for summarization
        """
        # Sort facts chronologically (oldest to newest)
        sorted_facts = self.sort_facts(facts, descending=False)

        # Format each fact with date prefix
        formatted_lines = []
        for fact in sorted_facts:
            pub_date = fact.get("publication_date")
            fact_text = fact.get("fact_text", "")

            if pub_date:
                date_str = pub_date.strftime(self.date_format)
                formatted_lines.append(f"({date_str}): {fact_text}")
            else:
                formatted_lines.append(f"(Date unknown): {fact_text}")

        # Join with newlines
        formatted_text = "\n".join(formatted_lines)

        return formatted_text


class AbstractiveSummarizer:
    """Generates abstractive summaries with inline citations.

    This class implements Phase 2b of the pipeline: abstractive summarization
    using a pre-trained transformer model (BART) with citation embedding.
    """

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

        # Lazy-load the summarization pipeline
        self._summarizer = None

    def _get_summarizer(self):
        """Lazy-load the summarization pipeline."""
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
            except ImportError:
                raise ImportError(
                    "transformers is required for AbstractiveSummarizer. "
                    "Install it with: pip install transformers torch"
                )

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

        # Generate summary using BART
        summarizer = self._get_summarizer()

        # Truncate input if too long (BART has 1024 token limit)
        max_input_chars = 4000  # Rough approximation
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
            # Fallback: use first few sentences
            summary_text = ". ".join(input_text.split(".")[:3]) + "."

        # Create citation mapping from source references
        citations = self._create_citations(facts)

        # Embed citations into summary (simple approach)
        summary_with_citations = self._embed_citations(summary_text, citations)

        return {
            "summary_text": summary_with_citations,
            "citations": citations,
        }

    def _create_citations(
        self,
        facts: list[dict[str, Any]]
    ) -> dict[int, list[dict[str, Any]]]:
        """Create citation mapping from facts.

        Args:
            facts: List of FactRecords with source_references

        Returns:
            Dictionary mapping citation numbers to source references
        """
        citations = {}
        citation_num = 1

        for fact in facts:
            source_refs = fact.get("source_references", [])
            if source_refs:
                citations[citation_num] = source_refs
                citation_num += 1

        return citations

    def _embed_citations(
        self,
        summary_text: str,
        citations: dict[int, list[dict[str, Any]]]
    ) -> str:
        """Embed citation markers into summary text.

        This is a simple implementation that appends citations at the end.
        For production, consider using NER to identify entities/facts and
        insert citations at appropriate positions.

        Args:
            summary_text: Generated summary
            citations: Citation mapping

        Returns:
            Summary with embedded citations
        """
        if not citations:
            return summary_text

        # Simple approach: append citation list at the end
        citation_markers = []
        for num in sorted(citations.keys()):
            source_refs = citations[num]
            if source_refs:
                # Use the first (most recent) source
                ref = source_refs[0]
                url = ref.get("source_url", "")
                citation_markers.append(f"[{num}]")

        # Append citation markers to summary
        if citation_markers:
            summary_with_citations = f"{summary_text} {' '.join(citation_markers)}"
        else:
            summary_with_citations = summary_text

        return summary_with_citations


class EntitySummaryStorage:
    """Manages storage of entity summaries in Delta Lake.

    This class implements Phase 3 of the pipeline: structured storage
    of entity summaries with citations in Delta Lake format.
    """

    def __init__(self, delta_manager=None):
        """Initialize the entity summary storage.

        Args:
            delta_manager: DeltaLakeManager instance (optional, will auto-create)
        """
        if delta_manager is None:
            from src.common.delta_lake import get_delta_manager
            delta_manager = get_delta_manager()

        self.delta = delta_manager
        self.table_name = "entity_summaries"

        # Define schema for entity summaries table
        self.schema = {
            "entity_name": "string",
            "entity_type": "string",
            "summary_text": "string",
            "source_references": "string",  # JSON-encoded list of sources
            "last_updated": "string",  # ISO 8601 timestamp
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

        # Flatten source references from citations
        source_refs_map = {}
        for citation_num, refs in citations.items():
            source_refs_map[str(citation_num)] = [
                {
                    "url": ref.get("source_url", ""),
                    "date": ref.get("publication_date").isoformat()
                    if ref.get("publication_date") else None,
                }
                for ref in refs
            ]

        # Find most recent publication date
        all_dates = []
        for fact in facts:
            if fact.get("publication_date"):
                all_dates.append(fact["publication_date"])

        last_updated = max(all_dates).isoformat() if all_dates else datetime.utcnow().isoformat()

        # Create record
        record = {
            "entity_name": entity_name,
            "entity_type": entity_type,
            "summary_text": summary_text,
            "source_references": json.dumps(source_refs_map),
            "last_updated": last_updated,
            "fact_count": len(facts),
            "created_at": datetime.utcnow().isoformat(),
        }

        # Write to Delta Lake
        self.delta.write(self.table_name, [record], mode="append")

        logger.info(
            f"✅ Saved summary for '{entity_name}' to Delta Lake table '{self.table_name}'"
        )

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
        # Build filter expression
        filters = []
        if entity_name:
            filters.append(f"entity_name = '{entity_name}'")
        if entity_type:
            filters.append(f"entity_type = '{entity_type}'")

        filter_expr = " AND ".join(filters) if filters else None

        # Read from Delta Lake
        records = self.delta.read(self.table_name, filters=filter_expr)

        return records


class Stage4EntityWorker:
    """Orchestrates the complete entity-centric summarization pipeline.

    This is the main entry point for Stage 4 processing. It coordinates all
    three phases of the pipeline:
    1. Fact aggregation and deduplication (FactAggregator)
    2. Chronological sorting and abstractive summarization (ChronologicalSorter + AbstractiveSummarizer)
    3. Structured storage (EntitySummaryStorage)
    """

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

    def process_documents(
        self,
        documents: list[dict[str, Any]]
    ):
        """Process a batch of documents through the entity summarization pipeline.

        Args:
            documents: List of document records with the following fields:
                - entity_name (str): Name of the entity
                - entity_type (str): Type of entity
                - content (str): Text content
                - source_url (str): Source URL
                - publication_date (datetime|str|None): Publication date
                - metadata (dict, optional): Additional metadata
        """
        logger.info(f"Processing {len(documents)} documents...")

        # Phase 1: Aggregate facts by entity
        for doc in documents:
            self.fact_aggregator.add_document(
                entity_name=doc["entity_name"],
                entity_type=doc["entity_type"],
                content=doc["content"],
                source_url=doc["source_url"],
                publication_date=doc.get("publication_date"),
                metadata=doc.get("metadata"),
            )

        # Get all deduplicated facts
        all_entity_facts = self.fact_aggregator.get_all_deduplicated_facts()

        # Phase 2 & 3: Summarize and store for each entity
        for entity_name, facts in all_entity_facts.items():
            if not facts:
                continue

            # Get entity type from first fact
            entity_type = facts[0].get("entity_type", "unknown")

            # Sort facts chronologically and prepare for summarization
            chronological_text = self.chronological_sorter.prepare_for_summarization(facts)

            # Generate abstractive summary with citations
            summary_result = self.summarizer.summarize(chronological_text, facts)

            # Store in Delta Lake
            self.storage.save_summary(
                entity_name=entity_name,
                entity_type=entity_type,
                summary_text=summary_result["summary_text"],
                citations=summary_result["citations"],
                facts=facts,
            )

            logger.info(
                f"✅ Completed processing for entity '{entity_name}' "
                f"({len(facts)} unique facts)"
            )

        logger.info(
            f"✅ Stage 4 processing complete. "
            f"Processed {len(all_entity_facts)} entities."
        )


# Example usage and integration
if __name__ == "__main__":
    # Example: Process sample documents
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
                "Professor Doe received the NSF CAREER Award in 2021. "  # Duplicate fact
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

    # Initialize worker
    worker = Stage4EntityWorker()

    # Process documents
    worker.process_documents(sample_documents)
