"""Flaky-test quarantine policy (#288).

A test marked ``@pytest.mark.flaky(reason="... #<issue>")`` is *quarantined*:
it is skipped in normal runs (CI included) and runs only with ``RUN_FLAKY=1``.
A flaky marker without an issue reference fails collection, so nothing gets
quarantined silently. See tests/README.md ("Flaky tests").
"""
from __future__ import annotations

import re

ISSUE_REF = re.compile(r"(?:^|[\s(])#\d+\b|github\.com/[^\s]+/issues/\d+")


def flaky_reason(marker) -> str:
    """The reason text of a ``flaky`` marker (keyword or first positional arg)."""
    reason = marker.kwargs.get("reason")
    if reason is None and marker.args:
        reason = marker.args[0]
    return str(reason or "")


def has_issue_ref(reason: str) -> bool:
    return bool(ISSUE_REF.search(reason))
