"""#483: the entity summarization example config is loadable, validated and wired."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.stage4 import entity_config as ec
from src.stage4.entity_config import (
    EXAMPLE_PATH,
    EntityConfigError,
    EntitySummarizationConfig,
    config_from_mapping,
    load_entity_config,
)
from src.stage4.entity_summarization import ChronologicalSorter, Stage4EntityWorker


class _Delta:
    def __init__(self):
        self.writes = []

    def write(self, table, rows, mode="append", **kw):
        self.writes.append((table, rows, mode))
        return True


class _Cfg:
    def __init__(self, raw):
        self.raw = raw

    def get(self, key, default=None):
        return self.raw.get(key, default)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(ec.ENV_VAR, raising=False)


def test_shipped_example_loads_without_edits():
    assert EXAMPLE_PATH.is_file()
    cfg = load_entity_config(EXAMPLE_PATH)
    raw = yaml.safe_load(EXAMPLE_PATH.read_text())["entity_summarization"]
    assert cfg.as_dict() == {**EntitySummarizationConfig().as_dict(), **raw}  # every example key honoured


def test_env_var_selects_the_file(tmp_path, monkeypatch):
    f = tmp_path / "entity.yml"
    f.write_text("similarity_threshold: 0.9\ndelta_table_name: custom_entities\n")  # bare mapping form
    monkeypatch.setenv(ec.ENV_VAR, str(f))
    cfg = load_entity_config()
    assert cfg.similarity_threshold == 0.9 and cfg.delta_table_name == "custom_entities"


def test_config_yml_block_then_defaults():
    assert load_entity_config(config=_Cfg({"entity_summarization": {"batch_size": 7}})).batch_size == 7
    assert load_entity_config(config=_Cfg({})) == EntitySummarizationConfig()


def test_explicit_path_beats_env(tmp_path, monkeypatch):
    env_file, explicit = tmp_path / "env.yml", tmp_path / "explicit.yml"
    env_file.write_text("batch_size: 1\n")
    explicit.write_text("batch_size: 2\n")
    monkeypatch.setenv(ec.ENV_VAR, str(env_file))
    assert load_entity_config(explicit).batch_size == 2


def test_invalid_yaml_fails_fast_naming_the_file(tmp_path):
    f = tmp_path / "broken.yml"
    f.write_text("entity_summarization:\n  batch_size: [1, 2\n")
    with pytest.raises(EntityConfigError, match=r"broken\.yml: invalid YAML"):
        load_entity_config(f)


def test_missing_file_fails_fast(tmp_path):
    with pytest.raises(EntityConfigError, match="cannot read"):
        load_entity_config(tmp_path / "nope.yml")


@pytest.mark.parametrize(
    "raw, match",
    [
        ({"similarty_threshold": 0.8}, "unknown entity_summarization key"),
        ({"similarity_threshold": 1.5}, "within \\[0, 1\\]"),
        ({"similarity_threshold": "high"}, "must be float"),
        ({"batch_size": True}, "must be int"),
        ({"batch_size": 0}, "batch_size must be >= 1"),
        ({"min_fact_length": 50, "max_fact_length": 40}, "must exceed min_fact_length"),
        ({"summary_min_length": 300, "summary_max_length": 300}, "must exceed summary_min_length"),
        ({"device": -2}, "device must be"),
        ({"input_source": "s3"}, "input_source must be one of"),
        ({"delta_write_mode": "overwrite"}, "delta_write_mode must be one of"),
        ({"citation_style": "footnote"}, "citation_style must be one of"),
        ({"log_level": "LOUD"}, "log_level must be one of"),
        ({"date_format": "no directives"}, "no date directives"),
        ({"enable_citations": "yes"}, "must be bool"),
        ({"embedding_model": ""}, "must be str"),
        (["not", "a", "mapping"], "expected a mapping"),
    ],
)
def test_invalid_values_rejected(raw, match):
    with pytest.raises(EntityConfigError, match=match):
        config_from_mapping(raw, "test.yml")


def test_from_config_wires_every_runtime_setting():
    cfg = config_from_mapping(
        {
            "embedding_model": "e-model",
            "summarization_model": "s-model",
            "similarity_threshold": 0.7,
            "min_fact_length": 5,
            "max_fact_length": 99,
            "summary_max_length": 150,
            "summary_min_length": 50,
            "device": 0,
            "delta_table_name": "my_entities",
            "date_format": "%d/%m/%Y",
            "enable_date_prefixes": False,
            "enable_citations": False,
        }
    )
    delta = _Delta()
    w = Stage4EntityWorker.from_config(cfg, delta_manager=delta)
    fa, cs, sm, st = w.fact_aggregator, w.chronological_sorter, w.summarizer, w.storage
    assert (fa.embedding_model_name, fa.similarity_threshold, fa.min_fact_length, fa.max_fact_length) == (
        "e-model", 0.7, 5, 99,
    )
    assert (cs.date_format, cs.enable_date_prefixes) == ("%d/%m/%Y", False)
    assert (sm.model_name, sm.max_length, sm.min_length, sm.device, sm.enable_citations) == (
        "s-model", 150, 50, 0, False,
    )
    assert st.table_name == "my_entities" and st.delta is delta
    assert w.config is cfg


def test_date_prefix_and_citation_switches_change_output():
    from datetime import datetime

    facts = [{"fact_text": "Joined UConn.", "publication_date": datetime(2020, 5, 1)}]
    assert ChronologicalSorter("%Y").prepare_for_summarization(facts) == "(2020): Joined UConn."
    assert ChronologicalSorter(enable_date_prefixes=False).prepare_for_summarization(facts) == "Joined UConn."

    from src.stage4.entity_summarization import AbstractiveSummarizer

    cited = [{"fact_text": "x", "source_references": [{"source_url": "https://a"}]}]
    for enabled, expected in ((True, "Summary. [1]"), (False, "Summary.")):
        s = AbstractiveSummarizer(enable_citations=enabled)
        s._summarizer = lambda text, **kw: [{"summary_text": "Summary."}]
        assert s.summarize("some input text", cited)["summary_text"] == expected


def test_storage_writes_to_configured_table():
    delta = _Delta()
    w = Stage4EntityWorker.from_config(config_from_mapping({"delta_table_name": "ents_v2"}), delta_manager=delta)
    w.storage.save_summary("Jane", "person", "s", {}, [])
    assert delta.writes[0][0] == "ents_v2" and delta.writes[0][2] == "append"


def test_examples_use_the_loader():
    root = Path(__file__).resolve().parents[3]
    for rel in ("examples/stage4/entity_worker_example.py", "examples/entity_summarization_demo.py"):
        text = (root / rel).read_text()
        assert "from_config(" in text, rel
