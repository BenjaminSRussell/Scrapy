"""Marker policy: load tests stay out of the PR selection (#287); the Stage 2 suite is
selectable with -m unit (#218)."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CI_SELECTION = "not slow and not kafka and not performance"


def _collect(*args: str) -> int:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-o", "addopts=", "-p", "no:cacheprovider", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=180, check=False,
    )
    out = r.stdout + r.stderr
    if "no tests collected" in out or re.search(r"\b0 selected\b", out):
        m = re.search(r"(\d+)/(\d+) tests collected", out)
        return int(m.group(1)) if m else 0
    m = re.search(r"(\d+)/(\d+) tests collected", out) or re.search(r"(\d+) tests? collected", out)
    assert m, out[-2000:]
    return int(m.group(1))


def test_performance_tests_excluded_from_ci_selection():
    assert _collect("tests/performance") > 0
    assert _collect("tests/performance", "-m", CI_SELECTION) == 0


def test_stage2_suite_fully_selected_by_unit_marker():
    total = _collect("tests/unit/stage2")
    assert total >= 10
    assert _collect("tests/unit/stage2", "-m", "unit") == total


def test_tests_readme_documents_real_layout_and_selection():  # #348
    text = (ROOT / "tests" / "README.md").read_text(encoding="utf-8")
    for needle in ('pytest tests/unit -m "not slow"', '-m "not kafka"', "OBS_OFFLINE=1", "--cov",
                   "-n auto", "make test-perf", "unit/", "integration/", "kafka/"):
        assert needle in text, needle
    for stale in ("tests/test_cache.py\n", "Target: 80%+ code coverage"):
        assert stale not in text
    for directory in ("unit", "integration", "kafka", "observability", "performance", "delta"):
        assert (ROOT / "tests" / directory).is_dir()
