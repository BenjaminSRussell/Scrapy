"""#568: root utilities live under scripts/; old paths are documented redirects.

cli.py stays the supported Python entrypoint in the project root. start.py and
shutdown.py are the stack lifecycle helpers and are unchanged.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
REPO = PROJECT.parent
MOVED = (
    "reseed.py",
    "drain_lake.py",
    "audit_file_counts.py",
    "monitor_and_trigger_depth.py",
)
HELM = PROJECT / "k8s" / "helm" / "scraping-pipeline" / "templates"
PRESTOP = (
    "import sys; sys.path.insert(0, '/app/scripts'); sys.path.insert(0, '/app'); "
    "from drain_lake import LakeDrainer; LakeDrainer().drain_transient_queues()"
)


def test_moved_utilities_are_not_root_python_entries():
    root_py = {p.name for p in PROJECT.glob("*.py")}
    assert "cli.py" in root_py
    assert root_py.isdisjoint(MOVED)
    for name in MOVED:
        path = PROJECT / "scripts" / name
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env python3")
        assert "deprecated" in text.lower()


def test_old_paths_documented_as_deprecated_redirects():
    scripts_readme = (PROJECT / "scripts" / "README.md").read_text(encoding="utf-8")
    for name in MOVED:
        assert f"| `python {name}` | `python scripts/{name}` |" in scripts_readme
    root_readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "python scripts/reseed.py" in root_readme
    assert "`python reseed.py` is deprecated" in root_readme
    contributing = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "python scripts/reseed.py" in contributing
    assert "deprecated redirect" in contributing


def test_reseed_relative_csv_resolves_from_project_root(monkeypatch, caplog):
    path = PROJECT / "scripts" / "reseed.py"
    source = path.read_text(encoding="utf-8")
    assert "Path(__file__).resolve().parent.parent / csv_path" in source
    spec = importlib.util.spec_from_file_location("_reseed_568", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(sys, "argv", ["scripts/reseed.py", "--csv", "data/raw/missing-issue-568.csv"])
    with pytest.raises(SystemExit) as exc:
        module.main()
    assert exc.value.code == 1
    expected = PROJECT / "data" / "raw" / "missing-issue-568.csv"
    assert str(expected) in caplog.text
    assert "scripts/data" not in caplog.text


def test_helm_prestop_imports_drain_lake_from_scripts():
    texts = [(HELM / name).read_text(encoding="utf-8") for name in (
        "scrapy-deployment.yaml",
        "stage-workers-deployments.yaml",
    )]
    blob = "\n".join(texts)
    assert blob.count(PRESTOP) == 4


def test_prestop_path_imports_lake_drainer():
    scripts = str(PROJECT / "scripts")
    sys.path.insert(0, scripts)
    sys.modules.pop("drain_lake", None)
    try:
        import drain_lake

        assert callable(drain_lake.LakeDrainer)
    finally:
        if sys.path and sys.path[0] == scripts:
            sys.path.pop(0)
