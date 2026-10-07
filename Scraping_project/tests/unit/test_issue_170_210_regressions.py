"""Regression guards for already-fixed bugs #170 and #210."""

import importlib

from src.stage2.stage2_worker import Stage2Worker
from src.utils.delta import DeltaHelper


def test_170_delta_helper_has_read_table_returning_rows(tmp_path):
    """Stage2Worker.run() calls delta.read_table(); DeltaHelper must provide it."""
    helper = DeltaHelper(tmp_path)
    assert callable(getattr(helper, "read_table", None))
    rows = helper.read_table("stage2_queue")
    assert isinstance(rows, list)
    assert hasattr(Stage2Worker, "run")


def test_210_metrics_exporter_imports_against_current_tree():
    """Exporter must not import legacy src.common.* modules."""
    module = importlib.import_module("monitoring.metrics_exporter")
    import inspect

    source = inspect.getsource(module)
    assert "src.common" not in source
