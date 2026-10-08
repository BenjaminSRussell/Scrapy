"""#948: the hop table matches the code, and config drift is reported."""

from __future__ import annotations

import importlib
import inspect
import logging
import re
from pathlib import Path

import pytest
import yaml

from src.core import constants as C
from src.core import pipeline_contract as pc
from src.core.config import Config

ROOT = Path(__file__).resolve().parents[2]

WRITE_RE = re.compile(r"\.(?:write|write_typed|merge_into)\(\s*(\"[a-z0-9_]+\"|[A-Z][A-Z0-9_]+)", re.S)
READ_RE = re.compile(r"\.(?:read|read_table|read_typed|count)\(\s*(\"[a-z0-9_]+\"|[A-Z][A-Z0-9_]+)", re.S)


def _resolve(mod, token: str) -> str | None:
    if token.startswith('"'):
        return token.strip('"')
    value = getattr(mod, token, None)
    return value if isinstance(value, str) else None


def _io(hop: pc.Hop) -> tuple[set[str], set[str]]:
    reads: set[str] = set()
    writes: set[str] = set()
    for name in hop.modules:
        mod = importlib.import_module(name)
        src = inspect.getsource(mod)
        writes |= {t for t in (_resolve(mod, m) for m in WRITE_RE.findall(src)) if t}
        reads |= {t for t in (_resolve(mod, m) for m in READ_RE.findall(src)) if t}
    return reads, writes


@pytest.mark.parametrize("hop", pc.HOPS, ids=lambda h: h.stage)
def test_every_code_write_is_declared(hop):
    _, writes = _io(hop)
    undeclared = writes - set(hop.writes)
    assert not undeclared, f"{hop.stage} writes {sorted(undeclared)} but HOPS doesn't declare it"


@pytest.mark.parametrize("hop", pc.HOPS, ids=lambda h: h.stage)
def test_declared_io_appears_in_code(hop):
    src = "\n".join(inspect.getsource(importlib.import_module(m)) for m in hop.modules)
    names_in_src = set(re.findall(r'"([a-z0-9_]+)"', src))
    consts = {getattr(C, n) for n in dir(C) if n.isupper() and isinstance(getattr(C, n), str) and n in src}
    for mod_name in hop.modules:
        mod = importlib.import_module(mod_name)
        consts |= {v for k, v in vars(mod).items() if k.isupper() and isinstance(v, str)}
    for table in hop.reads + hop.writes:
        assert table in names_in_src | consts, f"{hop.stage} declares {table} but its modules never mention it"


def test_stage3_writes_only_stage3_summaries_and_legacy_is_read_only():
    stage3 = next(h for h in pc.HOPS if h.stage == "stage3")
    assert stage3.writes == (C.TABLE_STAGE3_SUMMARIES,)
    for hop in pc.HOPS:
        _, writes = _io(hop)
        assert not (writes & pc.READ_ONLY_LEGACY), f"{hop.stage} writes a legacy read-only table"


def test_legacy_stage4_entry_point_writes_the_contract_table(monkeypatch):
    """large_doc_processor.process_queue used to write stage4_summaries (#948)."""
    from src.stage4 import large_doc_processor as ldp

    src = inspect.getsource(ldp)
    assert '"stage4_summaries"' not in src
    assert 'mode="overwrite"' not in src

    ran = {}

    async def fake_run(self):
        ran["processor"] = self.processor
        ran["delta"] = self.delta
        return 7

    from src.stage4 import stage4_worker

    monkeypatch.setattr(stage4_worker.Stage4Worker, "run", fake_run)
    proc = ldp.LargeDocProcessor.__new__(ldp.LargeDocProcessor)
    proc.delta = object()
    assert proc.process_queue() == 7
    assert ran["processor"] is proc and ran["delta"] is proc.delta


def test_hops_chain_stage_outputs_to_next_inputs():
    by_stage = {h.stage: h for h in pc.HOPS}
    assert C.TABLE_STAGE2_QUEUE in by_stage["stage1"].writes and C.TABLE_STAGE2_QUEUE in by_stage["stage2"].reads
    assert C.TABLE_STAGE2_PAGE_ANALYSIS in by_stage["stage2"].writes and C.TABLE_STAGE2_PAGE_ANALYSIS in by_stage["stage3"].reads
    assert C.TABLE_STAGE4_LARGE_DOCS in by_stage["stage2"].writes and C.TABLE_STAGE4_LARGE_DOCS in by_stage["stage4"].reads
    assert C.TABLE_JS_SPIDER_QUEUE in by_stage["stage1"].writes and C.TABLE_JS_SPIDER_QUEUE in by_stage["stage1-js"].reads


# --- config drift ----------------------------------------------------------------


def _cfg(tmp_path, data) -> Config:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(data))
    return Config(path)


def test_shipped_config_is_aligned():
    cfg = Config(ROOT / "config.yml")
    assert pc.contract_drift(cfg) == []
    declared = set((cfg.get("delta_lake.tables") or {}).keys())
    hop_tables = {t for h in pc.HOPS for t in h.reads + h.writes} - pc.READ_ONLY_LEGACY
    assert hop_tables <= declared, f"config.yml delta_lake.tables misses {sorted(hop_tables - declared)}"
    assert cfg.get("message_queues") is None


def test_rename_unknown_legacy_and_dead_queues_reported(tmp_path):
    cfg = _cfg(tmp_path, {
        "delta_lake": {"tables": {
            "stage3_summaries": "my_summaries",
            "stage9_magic": "stage9_magic",
            "stage4_summaries": "stage4_summaries",
        }},
        "message_queues": {"stage1_to_stage2": "stage1_discovered_urls"},
    })
    problems = pc.contract_drift(cfg)
    text = "\n".join(problems)
    assert "delta_lake.tables.stage3_summaries='my_summaries' is ignored" in text
    assert "delta_lake.tables.stage9_magic names a table no stage reads or writes" in text
    assert "stage4_summaries is a legacy read-only table" in text
    assert "message_queues is not used" in text
    assert len(problems) == 4


def test_non_mapping_tables_reported(tmp_path):
    cfg = _cfg(tmp_path, {"delta_lake": {"tables": ["stage2_queue"]}})
    assert any("must be a mapping" in p for p in pc.contract_drift(cfg))


def test_log_contract_drift_logs_once(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(pc, "_logged", False)
    cfg = _cfg(tmp_path, {"message_queues": {"a": "b"}})
    with caplog.at_level(logging.WARNING, logger="src.core.pipeline_contract"):
        assert len(pc.log_contract_drift(cfg)) == 1
        assert pc.log_contract_drift(cfg) == []
    assert caplog.text.count("[contract]") == 1


@pytest.mark.parametrize("module", ["src.stage2.stage2_worker", "src.stage3.stage3_worker", "src.stage4.stage4_worker"])
def test_workers_check_contract_at_startup(module):
    mod = importlib.import_module(module)
    fn = next(v for k, v in vars(mod).items() if k.startswith("run_stage") and inspect.iscoroutinefunction(v))
    assert "log_contract_drift()" in inspect.getsource(fn)


def test_readme_hop_table_matches_contract():
    readme = (ROOT / "README.md").read_text()
    assert pc.hop_table_markdown() in readme
