"""README and guide examples run against the real code (#152, #54, #139).

The root README used to document ConfigManager / StorageManager / URLProcessor
APIs under src/common/ that never existed. These tests keep the examples honest:
every Python block in the root README executes against a throwaway lake, every
guide block compiles and its imports resolve, and relative links point at files
that exist.
"""

from __future__ import annotations

import ast
import importlib
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

PROJECT = Path(__file__).resolve().parents[2]  # Scraping_project/
REPO = PROJECT.parent
ROOT_README = REPO / "README.md"
GUIDES = sorted((PROJECT / "docs" / "guides").glob("*.md"))
DOCS = [ROOT_README, *GUIDES]


def _python_blocks(path: Path) -> list[str]:
    # Blocks nested in list items are indented; dedent them like a renderer does.
    return [textwrap.dedent(b) for b in re.findall(r"```python\n(.*?)```", path.read_text(encoding="utf-8"), re.S)]


def _imported_modules(source: str) -> set[str]:
    mods: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
        elif isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
    return mods


def test_guides_exist():
    names = {p.name for p in GUIDES}
    assert {"README.md", "CONFIGURATION.md", "RUNNING.md", "MONITORING.md", "DATA_USAGE.md"} <= names


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name if p != ROOT_README else "root-README")
def test_python_blocks_compile_and_imports_resolve(doc):
    blocks = _python_blocks(doc)
    for block in blocks:
        compile(block, str(doc), "exec")
        for module in _imported_modules(block):
            importlib.import_module(module)


@pytest.mark.parametrize(
    "phantom",
    ["ConfigManager", "StorageManager", "src.common.config_manager", "src.common.storage_manager", "src.common.url_processor"],
)
def test_root_readme_has_no_phantom_api(phantom):
    # URLProcessor itself is real, but it lives in src.stage1.processors.
    assert phantom not in ROOT_README.read_text(encoding="utf-8")


def test_root_readme_does_not_document_missing_cli_commands():
    text = ROOT_README.read_text(encoding="utf-8")
    for gone in ("cli.py load_seeds", "cli.py list_seeds", "scrapy-app"):
        assert gone not in text, gone


LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name if p != ROOT_README else "root-README")
def test_relative_links_resolve(doc):
    text = doc.read_text(encoding="utf-8")
    broken = [
        target
        for target in LINK.findall(text)
        if "://" not in target and not target.startswith("mailto:") and not (doc.parent / target).exists()
    ]
    assert broken == []


def test_root_readme_examples_execute(tmp_path, monkeypatch):
    """Run every root-README Python block, in order, against an empty temp lake."""
    from src.lakehouse.lakehouse_manager import LakehouseManager

    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    monkeypatch.chdir(tmp_path)
    LakehouseManager.reset_instance()
    try:
        for block in _python_blocks(ROOT_README):
            exec(compile(block, "README.md", "exec"), {"__name__": "readme_example"})  # noqa: S102
        assert (tmp_path / "exports").exists()
    finally:
        LakehouseManager.reset_instance()


def test_readme_url_processing_outputs_are_accurate(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))
    monkeypatch.chdir(tmp_path)
    from src.stage1.processors.url_processor import URLProcessor

    p = URLProcessor(base_url="https://www.uconn.edu/", allowed_domains=["uconn.edu"])
    assert p.normalize_url("https://WWW.UConn.edu/About/?utm_source=x&b=2&a=1#top") == "https://www.uconn.edu/about?a=1&b=2"
    assert p.should_follow_url("https://www.uconn.edu/logo.png") is False
    assert p.deduplicate_urls(["https://www.uconn.edu/a", "https://www.uconn.edu/a?utm_source=x"]) == ["https://www.uconn.edu/a"]
    assert p.calculate_priority("https://www.uconn.edu/research/labs", value_score=85, depth=2) == 75


def test_running_guide_spider_args_match_base_spider():
    from src.stage1.experimental.base_spider import _as_list

    text = (PROJECT / "docs" / "guides" / "RUNNING.md").read_text(encoding="utf-8")
    m = re.search(r"-a allowed_domains=(\S+)", text)
    assert m and _as_list(m.group(1)) == ["example.org", "docs.example.org"]
