"""Contributor DX contracts (#335 #355 #356 #397 #398)."""
from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]  # Scraping_project/
REPO = ROOT.parent


def test_no_backup_workflows_in_actions_dir():  # #355
    wf = REPO / ".github" / "workflows"
    stray = [p.name for p in wf.iterdir() if not p.name.endswith((".yml", ".yaml"))]
    assert not stray, f"non-workflow files in .github/workflows: {stray}"


def _editorconfig() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    text = (ROOT / ".editorconfig").read_text(encoding="utf-8")
    cp.read_string("[__top__]\n" + text)
    return cp


def test_editorconfig_sections():  # #356
    cp = _editorconfig()
    assert cp["Makefile"]["indent_style"] == "tab"
    assert cp["*.md"]["trim_trailing_whitespace"] == "false"
    assert cp["*.json"]["indent_size"] == "2"
    yaml_section = next(s for s in cp.sections() if "yml" in s)
    assert "yaml" in yaml_section and cp[yaml_section]["indent_size"] == "2"  # covers docker-compose*.yml


def test_makefile_recipes_really_use_tabs():
    for i, line in enumerate((ROOT / "Makefile").read_text(encoding="utf-8").splitlines(), 1):
        assert not re.match(r"^ {2,}(pip|pytest|@echo|cd |\$\()", line), f"Makefile:{i} recipe indented with spaces"


@pytest.mark.parametrize("lock", ["requirements.txt", "dev-requirements.txt"])
def test_lockfile_headers_have_no_machine_paths(lock):  # #397
    header = [ln for ln in (ROOT / lock).read_text(encoding="utf-8").splitlines()[:8] if ln.startswith("#")]
    cmd = next(ln for ln in header if "pip-compile" in ln and "--output-file" in ln)
    assert not re.search(r"(/Users/|/home/|[A-Za-z]:\\\\)", cmd), cmd
    assert f"--output-file={lock}" in cmd


def test_make_lock_and_update_deps_compile_relative_paths():  # #397
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    for target in ("lock:", "update-deps:"):
        body = make[make.index("\n" + target) + 1:]
        body = body[: body.index("\n\n")]
        assert "--output-file=requirements.txt requirements.in" in body
        assert "--output-file=dev-requirements.txt dev-requirements.in" in body
    lock = make[make.index("\nlock:"):]
    assert "--upgrade --output-file" not in lock[: lock.index("\n\n")]


def test_contributing_covers_the_onboarding_checklist():  # #335 / #397
    text = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    for needle in ("Scraping_project", "python3.11 -m venv", "pip install -r requirements.txt -r dev-requirements.txt",
                   "pre-commit install", '-m "not slow and not kafka and not performance"', "make help",
                   "make lock", "make update-deps", "pull_request_template.md"):
        assert needle in text, needle


@pytest.mark.parametrize("readme", ["temp_scripts/README.md", "Scraping_project/temp_scripts/README.md"])
def test_temp_scripts_marked_unsupported(readme):  # #398
    text = (REPO / readme).read_text(encoding="utf-8").lower()
    assert "unsupported" in text and "not" in text
