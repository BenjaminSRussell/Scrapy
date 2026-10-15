"""#948: the hop table matches the code, and config drift is reported."""

from __future__ import annotations

import ast
import asyncio
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
REPO = ROOT.parent

_TABLE = re.compile(r"^[a-z][a-z0-9_]*$")


def _mentioned(mod) -> set[str]:
    """Table-name strings and uppercase string constants the module actually references."""
    src = inspect.getsource(mod)
    found = set(re.findall(r'["\']([a-z][a-z0-9_]*)["\']', src))
    for key, value in vars(mod).items():
        if isinstance(value, str) and key.isupper() and _TABLE.match(value) and key in src:
            found.add(value)
    for key, value in vars(C).items():
        if isinstance(value, str) and key.isupper() and _TABLE.match(value) and key in src:
            found.add(value)
    return found


@pytest.mark.parametrize("hop", pc.HOPS, ids=lambda hop: hop.stage)
def test_declared_io_appears_in_code(hop):
    mentioned: set[str] = set()
    for name in hop.modules:
        mentioned |= _mentioned(importlib.import_module(name))
    missing = [table for table in hop.reads + hop.writes if table not in mentioned]
    assert not missing, f"{hop.stage} declares {missing} but its modules never mention them"


def _call_tables(mod) -> set[str]:
    """String or constant table arguments to read/write/merge and BufferedDeltaBatch."""
    src = inspect.getsource(mod)
    tree = ast.parse(src)
    consts = {key: value for key, value in vars(mod).items() if isinstance(value, str)}
    consts.update({key: value for key, value in vars(C).items() if isinstance(value, str)})
    found: set[str] = set()

    def resolve(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _TABLE.match(node.value):
            return node.value
        if isinstance(node, ast.Name) and node.id in consts and _TABLE.match(consts[node.id]):
            return consts[node.id]
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            key = node.attr if node.value.id == "C" else None
            if key and key in consts and _TABLE.match(consts[key]):
                return consts[key]
        return None

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            else:
                name = ""
            if name in {"write", "write_typed", "read", "read_table", "read_typed", "merge_into", "count"} and node.args:
                table = resolve(node.args[0])
                if table:
                    found.add(table)
            if name == "BufferedDeltaBatch" and len(node.args) >= 2:
                table = resolve(node.args[1])
                if table:
                    found.add(table)
            # asyncio.to_thread(self.delta.merge_into, TABLE, ...)
            if name == "to_thread" and len(node.args) >= 2:
                target = node.args[0]
                target_name = target.attr if isinstance(target, ast.Attribute) else ""
                if target_name in {"write", "write_typed", "read", "read_table", "read_typed", "merge_into"}:
                    table = resolve(node.args[1])
                    if table:
                        found.add(table)
            self.generic_visit(node)

    Visitor().visit(tree)
    return found


@pytest.mark.parametrize("hop", pc.HOPS, ids=lambda hop: hop.stage)
def test_every_literal_table_call_is_declared(hop):
    for name in hop.modules:
        extra = _call_tables(importlib.import_module(name)) - set(hop.reads + hop.writes)
        assert not extra, f"{hop.stage} module {name} touches {sorted(extra)} outside HOPS"


def test_stage3_writes_configured_name_not_the_legacy_table(monkeypatch):
    from src.stage3.stage3_worker import Stage3Worker

    class _Cfg:
        def get(self, key, default=None):
            if key == "delta_lake.tables":
                return {"stage3_summaries": "custom_stage3"}
            return default

    monkeypatch.setattr("src.core.config.get_config", lambda: _Cfg())
    assert pc.stage3_summary_table() == "custom_stage3"

    delta_reads: list[str] = []

    class Delta:
        def read(self, table, **_):
            delta_reads.append(table)
            if table == "stage2_page_analysis":
                return [{
                    "url_hash": "h",
                    "url": "https://x/h",
                    "text_content": "word " * 200,
                    "is_low_quality": False,
                }]
            raise FileNotFoundError(table)

        def write(self, table, rows, mode="append", async_write=True):
            self.table = table
            return True

    delta = Delta()
    worker = Stage3Worker.__new__(Stage3Worker)
    worker.delta = delta
    worker.max_concurrent = 1
    worker.batch_size = 10
    worker.postgres = None
    worker.SIMILARITY_THRESHOLD = 0.3
    worker.budget_status = None

    async def _dedupe(batch):
        return batch

    async def _sum(doc):
        return {"url_hash": doc["url_hash"], "summary": "s"}

    monkeypatch.setattr(worker, "_deduplicate_documents", _dedupe)
    monkeypatch.setattr(worker, "_summarize_document", _sum)
    asyncio.run(worker._run_traced())
    assert delta.table == "custom_stage3"
    assert "custom_stage3" in delta_reads
    assert C.LEGACY_TABLE_STAGE3_SUMMARIES in delta_reads


def test_legacy_processor_does_not_write_stage4_summaries():
    from src.stage4 import large_doc_processor as ldp

    src = inspect.getsource(ldp)
    assert '"stage4_summaries"' not in src
    assert "TABLE_STAGE4_LARGE_DOC_SUMMARIES" in src


def test_hops_chain_stage_outputs_to_next_inputs():
    by_stage = {hop.stage: hop for hop in pc.HOPS}
    assert C.TABLE_STAGE2_QUEUE in by_stage["stage1"].writes
    assert C.TABLE_STAGE2_QUEUE in by_stage["stage2"].reads
    assert C.TABLE_STAGE2_PAGE_ANALYSIS in by_stage["stage2"].writes
    assert C.TABLE_STAGE2_PAGE_ANALYSIS in by_stage["stage3"].reads
    assert C.TABLE_STAGE4_LARGE_DOCS in by_stage["stage2"].writes
    assert C.TABLE_STAGE4_LARGE_DOCS in by_stage["stage4"].reads
    assert C.TABLE_JS_SPIDER_QUEUE in by_stage["stage1"].writes
    assert C.TABLE_JS_SPIDER_QUEUE in by_stage["stage1-js"].reads
    assert by_stage["stage3"].writes == (C.TABLE_STAGE3_SUMMARIES,)
    assert C.LEGACY_TABLE_STAGE3_SUMMARIES not in {t for hop in pc.HOPS for t in hop.writes}


def _cfg(tmp_path, data) -> Config:
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(data))
    return Config(path)


def test_shipped_config_is_aligned():
    cfg = Config(ROOT / "config.yml")
    assert pc.contract_drift(cfg) == []
    assert pc.stage3_summary_table(cfg) == C.TABLE_STAGE3_SUMMARIES
    assert cfg.get("message_queues") is None


def test_rename_unknown_and_dead_queues_reported(tmp_path):
    cfg = _cfg(tmp_path, {
        "delta_lake": {"tables": {
            "stage2_queue": "renamed_queue",
            "stage3_summaries": "my_summaries",
            "stage9_magic": "stage9_magic",
        }},
        "message_queues": {"stage1_to_stage2": "stage1_discovered_urls"},
    })
    text = "\n".join(pc.contract_drift(cfg))
    assert "delta_lake.tables.stage2_queue='renamed_queue' is ignored" in text
    assert "delta_lake.tables.stage3_summaries='my_summaries' is what Stage 3 writes" in text
    assert "delta_lake.tables.stage9_magic names a table no stage reads or writes" in text
    assert "message_queues is not used" in text
    assert "does not declare" in text


def test_non_mapping_tables_reported(tmp_path):
    cfg = _cfg(tmp_path, {"delta_lake": {"tables": ["stage2_queue"]}})
    assert any("must be a mapping" in problem for problem in pc.contract_drift(cfg))


def test_log_contract_drift_logs_once(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(pc, "_logged", False)
    tables = {table: table for table in {name for hop in pc.HOPS for name in hop.writes}}
    cfg = _cfg(tmp_path, {"delta_lake": {"tables": tables}, "message_queues": {"a": "b"}})
    with caplog.at_level(logging.WARNING, logger="src.core.pipeline_contract"):
        problems = pc.log_contract_drift(cfg)
        assert problems == [
            "config.yml message_queues is not used: no worker reads or writes those Redis lists "
            "(every hop is a Delta table; see src/core/pipeline_contract.HOPS). Remove the section"
        ]
        assert pc.log_contract_drift(cfg) == []
    assert caplog.text.count("[contract]") == 1


@pytest.mark.parametrize(
    "module",
    ["src.stage2.stage2_worker", "src.stage3.stage3_worker", "src.stage4.stage4_worker"],
)
def test_workers_check_contract_at_startup(module):
    mod = importlib.import_module(module)
    fn = next(value for key, value in vars(mod).items() if key.startswith("run_stage") and inspect.iscoroutinefunction(value))
    assert "log_contract_drift()" in inspect.getsource(fn)


def test_startup_validation_logs_the_contract():
    src = inspect.getsource(importlib.import_module("src.utils.validation"))
    assert "log_contract_drift(loaded)" in src


def test_stage_map_hop_table_matches_contract():
    stage_map = (REPO / "docs" / "STAGE_MAP.md").read_text()
    assert pc.hop_table_markdown() in stage_map
