import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Any

import numpy as np
from src.core.timeutil import utc_now, utc_now_iso

logger = logging.getLogger(__name__)


def entity_id_for(document: dict[str, Any]) -> str:
    """Stable id for a document. entity_id wins; otherwise the entity name."""
    raw = document.get("entity_id")
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    name = document.get("entity_name")
    if name is None:
        return ""
    return str(name).strip()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


def serialize_fact(fact: dict[str, Any]) -> dict[str, Any]:
    """Fact payload stored beside a summary. Cluster source refs are derived later."""
    stored = {key: value for key, value in fact.items() if key != "source_references"}
    return _json_safe(stored)


def deserialize_fact(payload: dict[str, Any]) -> dict[str, Any]:
    fact = dict(payload)
    for key in ("publication_date", "timestamp_added"):
        value = fact.get(key)
        if not isinstance(value, str):
            continue
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            fact[key] = datetime.fromisoformat(text)
        except ValueError:
            fact[key] = None
    if not isinstance(fact.get("metadata"), dict):
        fact["metadata"] = {}
    return fact


def decode_facts_json(raw: Any) -> list[dict[str, Any]]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        data = json.loads(raw)
    elif isinstance(raw, list):
        data = raw
    else:
        raise TypeError(f"facts_json must be a string or list, got {type(raw).__name__}")
    if not isinstance(data, list):
        raise ValueError("facts_json must be a list")
    return [deserialize_fact(item) for item in data if isinstance(item, dict)]


def merge_facts(prior: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Append facts, keeping the first copy of each (text, source) pair."""
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, Any]] = []
    for fact in prior + new:
        key = (str(fact.get("fact_text") or ""), str(fact.get("source_url") or ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(fact)
    return merged


def cluster_signature(facts: list[dict[str, Any]], clusters: list[list[int]]) -> str:
    """Membership of each semantic cluster. Unchanged means the summary input is the same."""
    groups: list[list[list[str]]] = []
    for indices in clusters:
        members = sorted(
            [str(facts[index].get("fact_text") or ""), str(facts[index].get("source_url") or "")]
            for index in indices
        )
        groups.append(members)
    groups.sort()
    return json.dumps(groups, ensure_ascii=False, separators=(",", ":"))


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

        # Lazily-loaded optional dependency (sentence_transformers).
        self._embedding_model: Any = None

        self.entity_facts: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def _get_embedding_model(self):
        if self._embedding_model is None:
            try:
                from sentence_transformers import SentenceTransformer

                logger.info(f"Loading embedding model: {self.embedding_model_name}")
                self._embedding_model = SentenceTransformer(self.embedding_model_name)
                logger.info(" Embedding model loaded successfully")
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
        facts = self.extract_facts(
            entity_name=entity_name,
            entity_type=entity_type,
            content=content,
            source_url=source_url,
            publication_date=publication_date,
            metadata=metadata,
        )
        self.entity_facts[entity_name].extend(facts)

        logger.info(f"Added {len(facts)} facts for entity '{entity_name}' from {source_url}")

    def extract_facts(
        self,
        entity_name: str,
        entity_type: str,
        content: str,
        source_url: str,
        publication_date: datetime | str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Sentences about ``entity_name``. Does not store them on the aggregator."""
        pub_date = self._parse_date(publication_date)
        entity_sentences = self._filter_entity_sentences(self._extract_sentences(content), entity_name)
        meta = metadata or {}
        return [
            {
                "entity_name": entity_name,
                "entity_type": entity_type,
                "fact_text": sentence,
                "source_url": source_url,
                "publication_date": pub_date,
                "metadata": meta,
                "timestamp_added": utc_now(),
            }
            for sentence in entity_sentences
        ]

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
        deduplicated, _clusters = self.deduplicate_fact_records(facts)
        logger.info(f" Deduplicated {len(facts)} facts → {len(deduplicated)} unique facts for '{entity_name}'")

        return deduplicated

    def deduplicate_fact_records(
        self, facts: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[list[int]]]:
        """Cluster ``facts`` without reading ``entity_facts``.

        The representative is a copy, so stored prior facts stay free of
        ``source_references`` (those are rebuilt on the next cluster).
        """
        if not facts:
            return [], []
        if len(facts) == 1:
            return list(facts), [[0]]

        fact_texts = [fact["fact_text"] for fact in facts]
        model = self._get_embedding_model()
        embeddings = model.encode(fact_texts, convert_to_numpy=True)
        clusters = self._cluster_facts(self._compute_similarity_matrix(embeddings))

        deduplicated = []
        for cluster_indices in clusters:
            cluster_facts = [facts[index] for index in cluster_indices]
            representative = dict(self._select_representative_fact(cluster_facts))
            representative["source_references"] = [
                {
                    "source_url": fact["source_url"],
                    "publication_date": fact["publication_date"],
                }
                for fact in cluster_facts
            ]
            deduplicated.append(representative)

        return deduplicated, clusters

    def _compute_similarity_matrix(self, embeddings: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / (norms + 1e-10)

        similarity: np.ndarray = np.dot(normalized, normalized.T)

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

    def __init__(self, date_format: str = "%Y-%m-%d", enable_date_prefixes: bool = True):
        self.date_format = date_format
        self.enable_date_prefixes = enable_date_prefixes

    def sort_facts(self, facts: list[dict[str, Any]], descending: bool = False) -> list[dict[str, Any]]:
        sorted_facts = sorted(facts, key=lambda f: f.get("publication_date") or datetime.min, reverse=descending)

        return sorted_facts

    def prepare_for_summarization(self, facts: list[dict[str, Any]]) -> str:
        sorted_facts = self.sort_facts(facts, descending=False)

        formatted_lines = []
        for fact in sorted_facts:
            pub_date = fact.get("publication_date")
            fact_text = fact.get("fact_text", "")

            if not self.enable_date_prefixes:
                formatted_lines.append(fact_text)
            elif pub_date:
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
        enable_citations: bool = True,
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
        self.enable_citations = enable_citations

        # Lazily-loaded optional dependency (transformers pipeline).
        self._summarizer: Any = None

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
                logger.info(" Summarization model loaded successfully")
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

        if self.enable_citations:
            summary_with_citations = self._embed_citations(summary_text, citations)
        else:
            summary_with_citations = summary_text

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

    def __init__(self, delta_manager=None, table_name: str = "entity_summaries"):
        if delta_manager is None:
            from src.utils.delta import get_delta

            delta_manager = get_delta()

        self.delta = delta_manager
        self.table_name = table_name

        self.schema = {
            "entity_id": "string",
            "entity_name": "string",
            "entity_type": "string",
            "summary_text": "string",
            "source_references": "string",
            "facts_json": "string",
            "cluster_signature": "string",
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
        *,
        entity_id: str | None = None,
        raw_facts: list[dict[str, Any]] | None = None,
        cluster_signature: str | None = None,
    ):
        """Save entity summary to Delta Lake.

        Args:
            entity_name: Name of the entity
            entity_type: Type of entity (person, organization, etc.)
            summary_text: Generated summary with citations
            citations: Citation mapping
            facts: Deduplicated facts used for the summary
            entity_id: Stable id. Defaults to ``entity_name`` for older callers.
            raw_facts: Facts to reload on the next incremental update (pre-cluster).
            cluster_signature: Cluster membership at the time of this summary.
        """
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

        last_updated = max(all_dates).isoformat() if all_dates else utc_now_iso()
        stored_facts = facts if raw_facts is None else raw_facts

        record = {
            "entity_id": entity_id or entity_name,
            "entity_name": entity_name,
            "entity_type": entity_type,
            "summary_text": summary_text,
            "source_references": json.dumps(source_refs_map),
            "facts_json": json.dumps([serialize_fact(fact) for fact in stored_facts], ensure_ascii=False),
            "cluster_signature": cluster_signature or "",
            "last_updated": last_updated,
            "fact_count": len(facts),
            "created_at": utc_now_iso(),
        }

        self.delta.write(self.table_name, [record], mode="append")

        logger.info(f" Saved summary for '{entity_name}' to Delta Lake table '{self.table_name}'")

    def _read_records(self) -> list[dict[str, Any]]:
        records = self.delta.read(self.table_name)
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]

    def latest_by_ids(self, entity_ids: set[str]) -> dict[str, dict[str, Any]]:
        """Newest summary row for each requested entity id (or legacy entity name)."""
        best: dict[str, tuple[tuple[str, str, int], dict[str, Any]]] = {}
        for index, record in enumerate(self._read_records()):
            stored_id = record.get("entity_id")
            if stored_id in entity_ids:
                entity_id = str(stored_id)
            elif not stored_id and record.get("entity_name") in entity_ids:
                entity_id = str(record.get("entity_name"))
            else:
                continue
            key = (str(record.get("created_at") or ""), str(record.get("last_updated") or ""), index)
            current = best.get(entity_id)
            if current is None or key >= current[0]:
                best[entity_id] = (key, record)
        return {entity_id: pair[1] for entity_id, pair in best.items()}

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
        # Filter in Python: the old SQL-string filter was not a valid deltalake
        # filter (and interpolated user input into an expression).
        records = self._read_records()
        if entity_name:
            records = [r for r in records if r.get("entity_name") == entity_name]
        if entity_type:
            records = [r for r in records if r.get("entity_type") == entity_type]
        return records

class Stage4EntityWorker:

    def __init__(
        self,
        embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2",
        summarization_model: str = "facebook/bart-large-cnn",
        similarity_threshold: float = 0.85,
        device: int = -1,
        *,
        min_fact_length: int = 20,
        max_fact_length: int = 500,
        summary_max_length: int = 300,
        summary_min_length: int = 100,
        date_format: str = "%Y-%m-%d",
        enable_date_prefixes: bool = True,
        enable_citations: bool = True,
        table_name: str = "entity_summaries",
        delta_manager=None,
    ):
        """Initialize the Stage 4 worker.

        Args:
            embedding_model: Sentence embedding model for deduplication
            summarization_model: Abstractive summarization model
            similarity_threshold: Threshold for semantic deduplication
            device: Device for model inference (-1 for CPU)

        Prefer ``Stage4EntityWorker.from_config()``, which reads the validated
        ``entity_summarization`` config (#483).
        """
        self.fact_aggregator = FactAggregator(
            embedding_model_name=embedding_model,
            similarity_threshold=similarity_threshold,
            min_fact_length=min_fact_length,
            max_fact_length=max_fact_length,
        )

        self.chronological_sorter = ChronologicalSorter(
            date_format=date_format, enable_date_prefixes=enable_date_prefixes
        )

        self.summarizer = AbstractiveSummarizer(
            model_name=summarization_model,
            max_length=summary_max_length,
            min_length=summary_min_length,
            device=device,
            enable_citations=enable_citations,
        )

        self.storage = EntitySummaryStorage(delta_manager=delta_manager, table_name=table_name)
        self.config: Any = None  # EntitySummarizationConfig when built via from_config()
        # entity_id -> {"facts", "signature", "summary"} after the first load.
        self._entity_state: dict[str, dict[str, Any]] = {}

        logger.info(" Stage4EntityWorker initialized")

    @classmethod
    def from_config(cls, cfg: Any = None, *, path: Any = None, delta_manager=None) -> "Stage4EntityWorker":
        """Worker built from ``EntitySummarizationConfig`` (default: ``load_entity_config(path)``)."""
        from src.stage4.entity_config import load_entity_config

        if cfg is None:
            cfg = load_entity_config(path)
        worker = cls(
            embedding_model=cfg.embedding_model,
            summarization_model=cfg.summarization_model,
            similarity_threshold=cfg.similarity_threshold,
            device=cfg.device,
            min_fact_length=cfg.min_fact_length,
            max_fact_length=cfg.max_fact_length,
            summary_max_length=cfg.summary_max_length,
            summary_min_length=cfg.summary_min_length,
            date_format=cfg.date_format,
            enable_date_prefixes=cfg.enable_date_prefixes,
            enable_citations=cfg.enable_citations,
            table_name=cfg.delta_table_name,
            delta_manager=delta_manager,
        )
        worker.config = cfg
        return worker

    def process_documents(self, documents: list[dict[str, Any]]):
        """Append facts for entities in this batch and rewrite only changed clusters.

        A document updates its own ``entity_id`` (or entity name, if no id is set).
        Other entities are not loaded, deduplicated, or summarized. Prior facts come
        from this worker's cache or the latest ``entity_summaries`` row (#474).
        """
        logger.info(f"Processing {len(documents)} documents...")
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for doc in documents:
            entity_id = entity_id_for(doc)
            if not entity_id:
                logger.warning("Skipping document with no entity_id or entity_name")
                continue
            grouped[entity_id].append(doc)

        self._hydrate_entities(list(grouped))
        rewritten = 0
        for entity_id, docs in grouped.items():
            if self._update_entity(entity_id, docs):
                rewritten += 1

        logger.info(
            f" Stage 4 processing complete. Rewrote {rewritten} of {len(grouped)} entities in this batch."
        )

    def _hydrate_entities(self, entity_ids: list[str]) -> None:
        missing = [entity_id for entity_id in entity_ids if entity_id not in self._entity_state]
        if not missing:
            return
        found = self.storage.latest_by_ids(set(missing))
        for entity_id in missing:
            record = found.get(entity_id)
            facts: list[dict[str, Any]] = []
            signature = None
            summary = None
            if record is not None:
                try:
                    facts = decode_facts_json(record.get("facts_json"))
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Prior facts for entity {entity_id!r} could not be read") from exc
                signature = record.get("cluster_signature") or None
                if record.get("summary_text"):
                    summary = record
            self._entity_state[entity_id] = {"facts": facts, "signature": signature, "summary": summary}

    def _update_entity(self, entity_id: str, documents: list[dict[str, Any]]) -> bool:
        """Merge ``documents`` into one entity. True when the summary was rewritten."""
        state = self._entity_state[entity_id]
        new_facts: list[dict[str, Any]] = []
        for doc in documents:
            new_facts.extend(
                self.fact_aggregator.extract_facts(
                    entity_name=doc["entity_name"],
                    entity_type=doc["entity_type"],
                    content=doc["content"],
                    source_url=doc["source_url"],
                    publication_date=doc.get("publication_date"),
                    metadata=doc.get("metadata"),
                )
            )
        combined = merge_facts(state["facts"], new_facts)
        deduped, clusters = self.fact_aggregator.deduplicate_fact_records(combined)
        signature = cluster_signature(combined, clusters)
        if state.get("signature") and signature == state["signature"] and state.get("summary"):
            logger.info(f"Entity {entity_id!r} cluster unchanged; summary not rewritten")
            state["facts"] = combined
            return False
        if not deduped:
            state["facts"] = combined
            state["signature"] = signature
            return False

        entity_name = str(deduped[0].get("entity_name") or entity_id)
        entity_type = str(deduped[0].get("entity_type") or "unknown")
        chronological_text = self.chronological_sorter.prepare_for_summarization(deduped)
        summary_result = self.summarizer.summarize(chronological_text, deduped)
        self.storage.save_summary(
            entity_name=entity_name,
            entity_type=entity_type,
            summary_text=summary_result["summary_text"],
            citations=summary_result["citations"],
            facts=deduped,
            entity_id=entity_id,
            raw_facts=combined,
            cluster_signature=signature,
        )
        state["facts"] = combined
        state["signature"] = signature
        state["summary"] = {"summary_text": summary_result["summary_text"], "entity_id": entity_id}
        logger.info(f" Completed processing for entity '{entity_name}' ({len(deduped)} unique facts)")
        return True

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

    worker = Stage4EntityWorker.from_config()  # $STAGE4_ENTITY_CONFIG or config.yml (#483)

    worker.process_documents(sample_documents)
