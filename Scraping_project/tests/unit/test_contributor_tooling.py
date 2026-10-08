"""Contributor tooling stays wired to the real project (#321, #323, #350, #363, #387)."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

PROJECT = Path(__file__).resolve().parents[2]
REPO = PROJECT.parent


def _jsonc(path: Path) -> dict:
    """Parse VS Code-style JSON with // comments (no comment markers inside strings here)."""
    text = "\n".join(re.sub(r"^\s*//.*$", "", line) for line in path.read_text().splitlines())
    return json.loads(text)


def _ci_pythons() -> list[str]:
    wf = yaml.safe_load((REPO / ".github/workflows/main.yml").read_text())
    for job in wf["jobs"].values():
        matrix = job.get("strategy", {}).get("matrix", {})
        if "python-version" in matrix:
            return [str(v) for v in matrix["python-version"]]
    raise AssertionError("no python-version matrix in main.yml")


def _nox_constant(name: str):
    tree = ast.parse((PROJECT / "noxfile.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not in noxfile.py")


def test_nox_matrix_matches_ci():
    assert _nox_constant("PYTHONS") == _ci_pythons()


def test_nox_markers_match_ci_default_selection():
    wf = (REPO / ".github/workflows/main.yml").read_text()
    markers = _nox_constant("PYTEST_MARKERS")
    assert f'-m "{markers}"' in wf


def test_devcontainer_opens_in_project_and_installs_requirements():
    dc = _jsonc(REPO / ".devcontainer/devcontainer.json")
    assert dc["workspaceFolder"].endswith("/Scraping_project")
    for req in ("requirements.txt", "dev-requirements.txt", "ci-tools.txt"):
        assert req in dc["postCreateCommand"] and (PROJECT / req).exists()
    assert any("docker-in-docker" in f for f in dc["features"])
    assert "3.11" in dc["image"]


def test_vscode_launch_and_tasks_point_at_real_paths():
    launch = _jsonc(REPO / ".vscode/launch.json")
    names = {c["name"] for c in launch["configurations"]}
    assert {"Python: current file", "pytest: current test file", "pytest: unit tests"} <= names
    for cfg in launch["configurations"]:
        assert cfg["cwd"] == "${workspaceFolder}/Scraping_project"
    tasks = _jsonc(REPO / ".vscode/tasks.json")
    labels = {t["label"]: t["command"] for t in tasks["tasks"]}
    assert "tests/unit" in labels["pytest: unit"]
    assert (PROJECT / "run_all_tests.sh").exists() and (PROJECT / "scripts/smoke_local.sh").exists()


def test_smoke_script_passes_and_fails_correctly(tmp_path):
    env = {**os.environ, "PYTHON": sys.executable, "SMOKE_TESTS": "tests/unit/test_contributor_tooling.py::test_nox_matrix_matches_ci"}
    ok = subprocess.run(["bash", "scripts/smoke_local.sh"], cwd=PROJECT, env=env, capture_output=True, text=True, timeout=300)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "SMOKE OK" in ok.stdout

    bad_test = tmp_path / "test_fail.py"
    bad_test.write_text("def test_x():\n    assert False\n")
    env["SMOKE_TESTS"] = str(bad_test)
    failed = subprocess.run(["bash", "scripts/smoke_local.sh"], cwd=PROJECT, env=env, capture_output=True, text=True, timeout=300)
    assert failed.returncode != 0 and "SMOKE FAILED" in failed.stdout


def test_smoke_script_no_tests_mode():
    env = {**os.environ, "PYTHON": sys.executable}
    out = subprocess.run(["bash", "scripts/smoke_local.sh", "--no-tests"], cwd=PROJECT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout
    assert "Fast offline test subset" not in out.stdout


def test_contributing_documents_tooling():
    text = (REPO / "CONTRIBUTING.md").read_text()
    for needle in ("scripts/smoke_local.sh", "nox", "playwright install chromium", ".devcontainer", "launch.json"):
        assert needle in text


# --- #357 / #360: one formatter path, one config file per tool -----------------


def test_precommit_uses_ruff_only_for_format_and_imports():
    cfg = yaml.safe_load((PROJECT / ".pre-commit-config.yaml").read_text())
    hook_ids = {h["id"] for repo in cfg["repos"] for h in repo["hooks"]}
    assert {"ruff", "ruff-format"} <= hook_ids
    assert not hook_ids & {"black", "isort", "flake8"}


def test_ruff_owns_import_sorting():
    import tomllib

    ruff = tomllib.loads((PROJECT / "ruff.toml").read_text())
    assert "I" in ruff["lint"]["select"]


def test_makefile_lint_matches_hooks():
    text = (PROJECT / "Makefile").read_text()
    lint = text.split("\nlint:", 1)[1].split("\n\n", 1)[0]
    assert "ruff check" in lint and "ruff format --check" in lint
    assert "black" not in lint and "isort" not in lint


def test_tool_config_not_duplicated_in_pyproject():
    import tomllib

    tool = tomllib.loads((PROJECT / "pyproject.toml").read_text()).get("tool", {})
    assert not {"black", "ruff", "mypy", "isort"} & set(tool)


def test_python_version_aligned_across_tools():
    import configparser
    import tomllib

    ruff = tomllib.loads((PROJECT / "ruff.toml").read_text())
    mypy = configparser.ConfigParser()
    mypy.read(PROJECT / "mypy.ini")
    oldest_ci = min(_ci_pythons(), key=lambda v: tuple(map(int, v.split("."))))
    assert ruff["target-version"] == "py" + oldest_ci.replace(".", "")
    assert mypy["mypy"]["python_version"] == oldest_ci
    assert (PROJECT / "docs/tooling.md").exists()
