"""#368: the src.common compatibility shim exports real symbols, loudly."""
from __future__ import annotations

import ast
import importlib
import sys
import warnings
from pathlib import Path

import pytest

SHIM = Path(__file__).resolve().parents[2] / "src" / "common" / "__init__.py"


def test_every_exported_name_exists():
    import src.common as common
    missing = [n for n in common.__all__ if not hasattr(common, n)]
    assert not missing


def test_get_delta_manager_delegates_once_with_a_deprecation_warning(monkeypatch):
    import src.common as common
    calls = []
    monkeypatch.setattr(common, "get_delta", lambda: calls.append(1) or "helper")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert common.get_delta_manager() == "helper"
    assert calls == [1]
    assert [w.category for w in caught] == [DeprecationWarning]


def test_no_wrapper_calls_itself():
    tree = ast.parse(SHIM.read_text())
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        called = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert fn.name not in called, f"{fn.name} calls itself"


def test_redis_manager_proxies_to_the_helper(monkeypatch):
    import src.common as common

    class Helper:
        ping = staticmethod(lambda: "pong")
    monkeypatch.setattr(common, "get_redis", lambda: Helper())
    with pytest.warns(DeprecationWarning):
        mgr = common.RedisManager()
    assert mgr.ping() == "pong"


def test_broken_reexport_raises_instead_of_an_import_warning(monkeypatch):
    """An ImportWarning is hidden by default and left an empty __all__ (#368)."""
    import src.utils.validation as validation
    monkeypatch.delattr(validation, "is_uconn_domain")
    saved = sys.modules.pop("src.common")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ImportWarning)
            with pytest.raises(ImportError):
                importlib.import_module("src.common")
    finally:
        sys.modules["src.common"] = saved


def test_shim_has_no_import_swallowing_handler():
    tree = ast.parse(SHIM.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Name):
            assert node.type.id != "ImportError"
