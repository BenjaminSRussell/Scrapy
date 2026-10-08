"""#499: the image workflow runs Trivy and fails on fixable CRITICAL findings."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / ".github" / "workflows" / "cd-release.yml"


def _steps():
    wf = yaml.safe_load(WF.read_text(encoding="utf-8"))
    return wf, wf["jobs"]["build-and-push"]["steps"]


def _trivy(steps):
    found = [s for s in steps if str(s.get("uses", "")).startswith("aquasecurity/trivy-action@")]
    assert len(found) == 1, "exactly one Trivy step expected"
    return found[0]


def test_trivy_step_fails_on_fixable_critical():
    _, steps = _steps()
    step = _trivy(steps)
    w = step["with"]
    assert w["severity"] == "CRITICAL"
    assert str(w["exit-code"]) == "1"
    assert w["ignore-unfixed"] is True
    assert w["image-ref"] == "${{ env.LOCAL_TAG }}"
    assert w["trivyignores"] == "Scraping_project/.trivyignore"
    assert "if" not in step  # runs on PRs and releases alike


def test_trivy_pinned_to_commit_sha():
    _, steps = _steps()
    ref = _trivy(steps)["uses"].split("@", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{40}", ref), "pin trivy-action to a full commit SHA, not a tag"


def test_scan_runs_after_build_and_before_push():
    _, steps = _steps()
    names = [s.get("name", "") for s in steps]
    scan = next(i for i, s in enumerate(steps) if "trivy-action" in str(s.get("uses", "")))
    build = next(i for i, n in enumerate(names) if n.startswith("Build "))
    push = next(i for i, n in enumerate(names) if n.startswith("Push "))
    assert build < scan < push


def test_trivyignore_change_triggers_scan_and_is_documented():
    wf, _ = _steps()
    paths = wf[True]["pull_request"]["paths"]  # PyYAML parses `on:` as True
    assert "Scraping_project/.trivyignore" in paths
    ignore = (ROOT / "Scraping_project/.trivyignore").read_text()
    assert "#499" in ignore and "cd-release.yml" in ignore
    for line in ignore.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            assert re.fullmatch(r"(CVE|GHSA)-[\w-]+", line), line
    readme = (ROOT / "Scraping_project/trivy-reports/README.md").read_text()
    assert ".trivyignore" in readme and "ignore-unfixed" in readme
