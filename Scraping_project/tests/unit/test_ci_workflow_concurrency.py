"""Guard the GitHub Actions anti-starvation policy (Oct 2026 main-CI backlog).

Every workflow: push triggers only on main or tags, a concurrency group per PR/ref
that cancels superseded runs for pull_request only, and a timeout on every job.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[3] / ".github" / "workflows").glob("*.yml"))
GROUP = "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}"
CANCEL = "${{ github.event_name == 'pull_request' }}"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_workflows_found():
    assert {p.name for p in WORKFLOWS} >= {"main.yml", "cd-release.yml", "ci-kafka-alerts.yml"}


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_push_only_on_main_or_tags(path):
    push = _load(path)[True].get("push")  # PyYAML parses `on:` as True
    if push is None:
        return
    assert set(push) <= {"branches", "tags", "paths"}, push
    assert push.get("branches", ["main"]) == ["main"], "feature branches run via pull_request only"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_concurrency_cancels_superseded_pr_runs_only(path):
    conc = _load(path).get("concurrency")
    assert conc == {"group": GROUP, "cancel-in-progress": CANCEL}, conc


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_has_a_timeout(path):
    jobs = _load(path)["jobs"]
    missing = [name for name, job in jobs.items() if not job.get("timeout-minutes")]
    assert not missing, f"jobs without timeout-minutes: {missing}"
