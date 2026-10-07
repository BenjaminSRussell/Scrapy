"""#621 ephemeral-lake opt-in and #457 example imports."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]


def test_env_selected_memory_backend_needs_opt_in(monkeypatch):  # 621
    from src.lakehouse import lakehouse_manager as lm

    monkeypatch.setenv("DELTA_BACKEND", "memory")
    monkeypatch.delenv("ALLOW_INMEMORY_DELTA", raising=False)
    with pytest.raises(RuntimeError, match="ALLOW_INMEMORY_DELTA"):
        lm.get_lakehouse_manager()


def test_env_selected_memory_backend_with_opt_in(monkeypatch):  # 621
    from src.lakehouse import lakehouse_manager as lm

    monkeypatch.setenv("DELTA_BACKEND", "memory")
    monkeypatch.setenv("ALLOW_INMEMORY_DELTA", "1")
    assert isinstance(lm.get_lakehouse_manager(), lm.InMemoryBackend)


def test_explicit_memory_mode_is_unaffected(monkeypatch):  # 621
    from src.lakehouse import lakehouse_manager as lm

    monkeypatch.delenv("DELTA_BACKEND", raising=False)
    monkeypatch.delenv("ALLOW_INMEMORY_DELTA", raising=False)
    assert isinstance(lm.get_lakehouse_manager("memory"), lm.InMemoryBackend)


def _src_imports(path: Path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("src"):
            yield node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("src"):
                    yield alias.name


@pytest.mark.parametrize(
    "example", sorted((PROJECT / "examples").rglob("*.py")), ids=lambda p: p.name
)
def test_examples_import_only_existing_src_modules(example, monkeypatch):  # 457
    monkeypatch.syspath_prepend(str(PROJECT))
    missing = [m for m in _src_imports(example) if importlib.util.find_spec(m) is None]
    assert not missing, f"{example.name} imports modules that no longer exist: {missing}"
