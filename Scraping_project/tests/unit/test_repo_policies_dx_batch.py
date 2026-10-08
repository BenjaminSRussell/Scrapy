"""Repository policy / DX files stay present, linked and consistent.

#669 SECURITY.md, #746 CODE_OF_CONDUCT.md, #747/#692 CHANGELOG + release process,
#715 ADRs, #748 .python-version, #783 .envrc example, #694 devcontainer,
#784 py.typed, #639 examples/README, #627 scrapy.cfg docs.
"""
from __future__ import annotations

import configparser
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]  # Scraping_project/
REPO = ROOT.parent


def _read(path: Path) -> str:
    assert path.is_file(), f"missing {path.relative_to(REPO)}"
    return path.read_text(encoding="utf-8")


def _links(md: str) -> set[str]:
    return {m.split("#")[0] for m in re.findall(r"\]\(([^)\s]+)\)", md) if not m.startswith(("http", "#", "mailto:"))}


@pytest.mark.parametrize("doc", ["SECURITY.md", "CODE_OF_CONDUCT.md", "CHANGELOG.md", "docs/adr/README.md"])
def test_policy_docs_linked_from_readme_and_contributing(doc):
    for index in ("README.md", "CONTRIBUTING.md"):
        assert doc in _links(_read(REPO / index)), f"{doc} not linked from {index}"


def test_security_policy_has_contact_and_supported_versions():  # #669
    text = _read(REPO / "SECURITY.md")
    assert "security/advisories/new" in text
    assert "Security contact request" in text  # works even when private reporting is off
    assert "## Supported versions" in text and "`main`" in text


def test_code_of_conduct_adopts_covenant_with_contact():  # #746
    text = _read(REPO / "CODE_OF_CONDUCT.md")
    assert "Contributor Covenant" in text and "2.1" in text
    assert "## Reporting" in text and "@BenjaminSRussell" in text


def test_changelog_format_and_release_checklist():  # #747 #692
    text = _read(REPO / "CHANGELOG.md")
    assert "keepachangelog.com" in text and "semver.org" in text
    assert "## [Unreleased]" in text
    releasing = _read(REPO / "docs" / "RELEASING.md")
    assert "## Versioning policy" in releasing and "## Release checklist" in releasing
    assert "CHANGELOG.md" in releasing and "git tag -a v" in releasing
    notes = yaml.safe_load(_read(REPO / ".github" / "release.yml"))
    assert notes["changelog"]["categories"][-1]["labels"] == ["*"]


def test_adr_template_and_index_cover_every_record():  # #715
    adr = REPO / "docs" / "adr"
    assert (adr / "0000-template.md").is_file()
    index = _read(adr / "README.md")
    records = sorted(p.name for p in adr.glob("[0-9][0-9][0-9][0-9]-*.md") if p.name != "0000-template.md")
    assert records, "need at least one ADR"
    for name in records:
        assert name in index, f"{name} missing from docs/adr/README.md index"
        assert re.search(r"^- \*\*Status:\*\* ", _read(adr / name), re.M), f"{name} has no Status line"


def test_python_version_pin_is_tested_in_ci():  # #748
    pin = _read(REPO / ".python-version").strip()
    workflow = _read(REPO / ".github" / "workflows" / "main.yml")
    matrix = re.search(r"python-version:\s*\[([^\]]+)\]", workflow)
    assert matrix, "main.yml lost its python-version matrix"
    assert f'"{pin}"' in matrix.group(1), f".python-version {pin} not in CI matrix {matrix.group(1)}"
    assert f"python:{pin}" in _read(ROOT / "Dockerfile")


def test_envrc_example_matches_pytest_paths_and_holds_no_secrets():  # #783
    text = _read(ROOT / ".envrc.example")
    assert ".venv/bin/activate" in text and "dotenv_if_exists .env" in text
    assert 'PYTHONPATH="$PWD:$PWD/src' in text
    ini = configparser.ConfigParser()
    ini.read(ROOT / "pytest.ini")
    assert ini["pytest"]["pythonpath"].split() == [".", "src"]
    assert not re.search(r"(?i)(password|secret|token|api_key)\s*=", text)


def _git_ignored(path: str) -> bool:
    proc = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO, capture_output=True)
    if proc.returncode not in (0, 1):
        pytest.skip("not a git checkout")
    return proc.returncode == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_ignore_rules_keep_devcontainer_and_drop_personal_envrc():  # #694 #783
    assert not _git_ignored(".devcontainer/devcontainer.json")
    assert not _git_ignored("Scraping_project/.envrc.example")
    assert _git_ignored("Scraping_project/.envrc")


def test_devcontainer_definition_is_consistent():  # #694
    raw = _read(REPO / ".devcontainer" / "devcontainer.json")
    spec = json.loads(re.sub(r"^\s*//.*$", "", raw, flags=re.M))
    compose = yaml.safe_load(_read(REPO / ".devcontainer" / spec["dockerComposeFile"]))
    assert spec["service"] in compose["services"] and "redis" in compose["services"]
    pin = _read(REPO / ".python-version").strip()
    assert f"python:1-{pin}" in compose["services"][spec["service"]]["image"]
    assert spec["containerEnv"]["REDIS_HOST"] == "redis"
    assert spec["workspaceFolder"].endswith("/Scraping_project")
    assert "requirements.txt -r dev-requirements.txt -r ci-tools.txt" in spec["postCreateCommand"]
    assert "|| true" not in spec["postCreateCommand"], "install failures must not be masked"


def test_py_typed_marker_shipped():  # #784
    assert (ROOT / "src" / "py.typed").is_file()
    assert "include src/py.typed" in _read(ROOT / "MANIFEST.in")


def test_examples_readme_lists_every_example():  # #639
    text = _read(ROOT / "examples" / "README.md")
    assert "requirements-stage4.txt" in text
    for script in sorted((ROOT / "examples").rglob("*.py")):
        rel = script.relative_to(ROOT / "examples").as_posix()
        assert rel in text, f"examples/{rel} not documented in examples/README.md"


def test_scrapy_cfg_documented():  # #627
    cfg = configparser.ConfigParser()
    cfg.read(ROOT / "scrapy.cfg")
    assert cfg["settings"]["default"] == "src.settings"
    assert "unsupported" in _read(ROOT / "scrapy.cfg").lower().replace("not supported", "unsupported")
    contributing = _read(REPO / "CONTRIBUTING.md")
    assert "cd Scraping_project\nscrapy list" in contributing
    assert cfg["deploy"]["project"] in contributing and "BOT_NAME" in contributing
