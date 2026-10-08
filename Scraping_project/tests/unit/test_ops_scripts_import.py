"""#284: ops scripts must import (no references to the removed src.common.delta_lake)."""

import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = [
    "scripts/load_seeds.py", "scripts/reset_lake.py", "scripts/vacuum_delta_tables.py", "reseed.py", "cli.py",
    "drain_lake.py",  # #522: imported src.common.config / redis_manager, which no longer exist
]
REMOVED = ("src.common.delta_lake", "src.common.constants", "src.common.config", "src.common.redis_manager")


@pytest.mark.parametrize("rel", SCRIPTS)
def test_script_imports(rel):
    spec = importlib.util.spec_from_file_location(f"_ops_{Path(rel).stem}", ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # guarded by __main__, so nothing runs


@pytest.mark.parametrize("rel", SCRIPTS)
def test_no_removed_module_references(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    names = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module]
    names += [c.args[0].value for c in ast.walk(tree)
              if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "import_module"
              and c.args and isinstance(c.args[0], ast.Constant)]
    assert not [n for n in names if n.startswith(REMOVED)]


def test_vacuum_helper_resolves_manager(monkeypatch):
    import src.lakehouse.lakehouse_manager as lm

    sentinel = object()
    monkeypatch.setattr(lm, "get_delta_manager", lambda: sentinel)
    spec = importlib.util.spec_from_file_location("_ops_vacuum", ROOT / "scripts/vacuum_delta_tables.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module._get_delta_manager() is sentinel
