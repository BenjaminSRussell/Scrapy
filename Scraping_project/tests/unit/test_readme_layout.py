"""READMEs match the disk: project tree, spider names, and the working directory (#334, #489)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from scrapy.settings import Settings
from scrapy.spiderloader import SpiderLoader

PROJECT = Path(__file__).resolve().parents[2]  # Scraping_project/
REPO = PROJECT.parent
PROJECT_README = (PROJECT / "README.md").read_text(encoding="utf-8")
ROOT_README = (REPO / "README.md").read_text(encoding="utf-8")


def _section_code_block(text: str, heading: str) -> str:
    after = text[text.index(heading):]
    return after.split("```", 2)[1]


def _tree_paths(block: str) -> list[str]:
    paths, stack = [], []
    for line in block.splitlines():
        m = re.match(r"^((?:│   |    )*)(?:├── |└── )(\S+)", line)
        if not m:
            continue
        depth = len(m.group(1)) // 4
        name = m.group(2)
        stack = stack[:depth] + [name.rstrip("/")]
        paths.append("/".join(stack))
    return paths


def _spiders() -> dict[str, str]:
    loader = SpiderLoader.from_settings(Settings({"SPIDER_MODULES": ["src.stage1"]}))
    return {name: loader.load(name).__module__.replace(".", "/") + ".py" for name in loader.list()}


def test_project_tree_matches_disk():
    paths = _tree_paths(_section_code_block(PROJECT_README, "## Project Structure"))
    assert len(paths) > 30
    missing = [p for p in paths if not (PROJECT / p).exists()]
    assert missing == [], f"README tree lists paths that do not exist: {missing}"


def test_tree_shows_spiders_where_they_live():
    paths = set(_tree_paths(_section_code_block(PROJECT_README, "## Project Structure")))
    for module in _spiders().values():
        assert module in paths, f"{module} missing from the README tree"
    assert "src/stage1/depth_spider.py" not in paths


@pytest.mark.parametrize("readme", [PROJECT_README, ROOT_README], ids=["project", "root"])
def test_spider_tables_match_scrapy_names_and_modules(readme):
    rows = dict(re.findall(r"^\|\s*`([a-z_]+)`\s*\|\s*`(src/stage1/[^`]+\.py)`", readme, re.M))
    assert rows == _spiders()


def test_scrapy_list_is_documented():
    assert "scrapy list" in PROJECT_README and "scrapy list" in ROOT_README


PROJECT_CMD = re.compile(
    r"^\s*(python3? (start|cli|reseed|shutdown)\.py|pip install -r|pytest|docker-compose|docker compose|"
    r"ruff check|mypy src|scrapy |pre-commit|make )",
    re.M,
)


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```(?:bash|sh)\n(.*?)```", text, re.S)


def test_root_readme_has_the_working_directory_callout():
    assert "Always work from `Scraping_project/`" in ROOT_README


def test_every_root_readme_command_block_names_its_working_directory():
    offenders = [
        block.strip().splitlines()[0]
        for block in _bash_blocks(ROOT_README)
        if PROJECT_CMD.search(block)
        and "cd Scraping_project" not in block
        and "# from Scraping_project/" not in block
        and "Scraping_project/" not in block
    ]
    assert offenders == []


def test_quick_start_cds_into_the_project():
    quick = ROOT_README[ROOT_README.index("## 🚀 Quick Start"):]
    first = _bash_blocks(quick)[0]
    assert first.strip().splitlines()[0] == "cd Scraping_project"
    assert (PROJECT / "start.py").exists() and not (REPO / "start.py").exists()
