"""Optional Playwright Chromium layer stays out of default CI and release images (#644)."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # Scraping_project/
REPO = ROOT.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
WF = REPO / ".github" / "workflows" / "ci-js-playwright.yml"


def _stage_block(name: str) -> str:
    start = re.search(rf"^FROM\s+\S+\s+as\s+{re.escape(name)}\s*$", DOCKERFILE, re.I | re.M)
    assert start, name
    nxt = re.search(r"^FROM\s", DOCKERFILE[start.end() :], re.M)
    return DOCKERFILE[start.start() : start.end() + (nxt.start() if nxt else len(DOCKERFILE))]


def test_playwright_stage_installs_chromium_for_the_scraper_user():
    block = _stage_block("playwright")
    assert "FROM base as playwright" in block
    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in block
    assert "python -m playwright install --with-deps chromium" in block
    assert "chown -R scraper:scraper /ms-playwright" in block
    assert block.strip().endswith("USER scraper")


def test_core_and_release_stages_do_not_install_a_browser():
    for name in ("base", "production", "core", "stage3", "ml", "stage4", "crawler", "metrics"):
        assert "playwright install" not in _stage_block(name), name
    cd = yaml.safe_load((REPO / ".github" / "workflows" / "cd-release.yml").read_text(encoding="utf-8"))
    images = [entry["image"] for entry in cd["jobs"]["build-and-push"]["strategy"]["matrix"]["include"]]
    assert "playwright" not in images
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    targets = set()
    for svc in compose["services"].values():
        build = svc.get("build")
        if isinstance(build, dict):
            targets.add(build.get("target"))
    assert "playwright" not in targets
    assert "production" in targets


def test_manual_ci_label_is_opt_in_and_requires_the_smoke():
    doc = yaml.safe_load(WF.read_text(encoding="utf-8"))
    triggers = doc[True]  # PyYAML parses the `on:` key as boolean True
    assert "push" not in triggers
    assert "workflow_dispatch" in triggers
    pull_request = triggers["pull_request"]
    assert pull_request["branches"] == ["main"]
    assert pull_request["types"] == ["labeled"]
    job = doc["jobs"]["chromium-smoke"]
    assert job["if"] == ("github.event_name == 'workflow_dispatch' || github.event.label.name == 'ci:playwright'")
    assert job["timeout-minutes"] >= 15
    text = WF.read_text(encoding="utf-8")
    assert "python -m playwright install --with-deps chromium" in text
    assert "PLAYWRIGHT_REQUIRE_BROWSER" in text
    assert "tests/unit/stage1/test_js_playwright_browser_644.py" in text
    assert "-m playwright" in text
    main = (REPO / ".github" / "workflows" / "main.yml").read_text(encoding="utf-8")
    assert "playwright install" not in main


def test_docs_name_the_target_and_the_label():
    rendering = (ROOT / "docs" / "JS_RENDERING.md").read_text(encoding="utf-8")
    contributing = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    readme = (ROOT / "tests" / "README.md").read_text(encoding="utf-8")
    for text in (rendering, contributing):
        assert "ci:playwright" in text
        assert "--target playwright" in text
    assert "playwright:" in (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "test_js_playwright_browser_644.py" in readme
    assert "Requires scrapy-playwright installation" in (
        ROOT / "tests" / "unit" / "stage1" / "test_js_playwright_browser_644.py"
    ).read_text(encoding="utf-8")
