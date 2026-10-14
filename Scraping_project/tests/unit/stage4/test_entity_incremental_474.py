"""Incremental Stage 4 entity updates (#474). Fixture corpus only; no scrape."""

import json
from pathlib import Path

import numpy as np
import yaml

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "stage4" / "entity_corpus_474.yml"


class MemoryDelta:
    def __init__(self):
        self.rows: list[dict] = []

    def write(self, table, records, mode="append"):
        assert table == "entity_summaries"
        if mode == "overwrite":
            self.rows = list(records)
        else:
            self.rows.extend(records)
        return True

    def read(self, table, filters=None, columns=None):
        assert table == "entity_summaries"
        return list(self.rows)


class DistinctEmbed:
    """One-hot vectors so different fact texts never share a cluster."""

    def __init__(self):
        self.batches: list[list[str]] = []

    def encode(self, texts, convert_to_numpy=True):
        texts = [str(text) for text in texts]
        self.batches.append(list(texts))
        dim = max(8, len(texts))
        index: dict[str, int] = {}
        for text in texts:
            if text not in index:
                index[text] = len(index)
        rows = []
        for text in texts:
            vec = np.zeros(dim, dtype=float)
            vec[index[text]] = 1.0
            rows.append(vec)
        return np.vstack(rows)


def _worker(delta, embed, calls):
    from src.stage4.entity_summarization import Stage4EntityWorker

    worker = Stage4EntityWorker(delta_manager=delta)
    worker.fact_aggregator._embedding_model = embed

    def summarize(input_text, facts):
        name = facts[0]["entity_name"] if facts else input_text
        calls.append(name)
        return {"summary_text": f"Summary of {name}.", "citations": {}}

    worker.summarizer.summarize = summarize
    return worker


def _rows(delta, entity_id):
    return [row for row in delta.rows if row.get("entity_id") == entity_id]


def test_one_document_updates_only_that_entity_and_skips_unchanged_cluster():
    corpus = yaml.safe_load(FIXTURE.read_text())
    delta = MemoryDelta()
    calls: list[str] = []

    first = _worker(delta, DistinctEmbed(), calls)
    first.process_documents(corpus["documents"])

    assert calls == ["Jane Doe", "UConn AI Lab"]
    assert len(_rows(delta, "person-jane-doe")) == 1
    assert len(_rows(delta, "org-ai-lab")) == 1
    lab_summary = _rows(delta, "org-ai-lab")[0]["summary_text"]
    jane_before = json.loads(_rows(delta, "person-jane-doe")[0]["facts_json"])
    assert any("faculty member" in fact["fact_text"] for fact in jane_before)
    assert any("Full Professor" in fact["fact_text"] for fact in jane_before)

    calls.clear()
    second_embed = DistinctEmbed()
    second = _worker(delta, second_embed, calls)
    second.process_documents([corpus["incremental_document"]])

    assert calls == ["Jane Doe"]
    encoded = [text for batch in second_embed.batches for text in batch]
    assert any("faculty member" in text for text in encoded)
    assert any("fifteen doctoral" in text for text in encoded)
    assert all("established in 2022" not in text for text in encoded)
    assert len(_rows(delta, "org-ai-lab")) == 1
    assert _rows(delta, "org-ai-lab")[0]["summary_text"] == lab_summary

    jane_rows = _rows(delta, "person-jane-doe")
    assert len(jane_rows) == 2
    latest_facts = json.loads(jane_rows[-1]["facts_json"])
    latest_text = " ".join(fact["fact_text"] for fact in latest_facts)
    assert "faculty member" in latest_text
    assert "fifteen doctoral" in latest_text
    assert len(latest_facts) == len(jane_before) + 1
    assert jane_rows[-1]["cluster_signature"] != jane_rows[0]["cluster_signature"]

    calls.clear()
    third_embed = DistinctEmbed()
    third = _worker(delta, third_embed, calls)
    third.process_documents([corpus["incremental_document"]])

    assert calls == []
    assert len(delta.rows) == 3
    assert third_embed.batches
    assert all("established in 2022" not in text for batch in third_embed.batches for text in batch)
