"""Cross-repo Delta contracts match the writers and SCHEMA_REGISTRY (#1099)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_delta_contracts.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_delta_contracts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
@pytest.mark.contract
def test_registered_contracts_match_writers():
    module = _load()
    assert module.check() == []


@pytest.mark.unit
@pytest.mark.contract
def test_core_tables_are_registered():
    module = _load()
    registry = module.load_registry()
    names = {entry["name"] for entry in registry["contracts"]}
    tables = {entry["delta_table"] for entry in registry["contracts"]}
    assert names >= {"pages", "extractions", "jobs"}
    assert tables >= {"stage2_page_analysis", "metadata_queue", "stage2_queue"}
