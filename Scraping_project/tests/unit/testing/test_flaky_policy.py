"""Flaky quarantine policy (#288): issue reference required, skipped unless RUN_FLAKY=1."""
from __future__ import annotations

import pytest

from tests import conftest
from tests.flaky_policy import flaky_reason, has_issue_ref


class _Item:
    def __init__(self, nodeid, marker=None):
        self.nodeid = nodeid
        self._marker = marker
        self.added = []

    def get_closest_marker(self, name):
        return self._marker if name == "flaky" else None

    def add_marker(self, marker):
        self.added.append(marker)


@pytest.mark.parametrize("reason,ok", [
    ("races the redis pool, #1234", True),
    ("#88", True),
    ("see https://github.com/BenjaminSRussell/Scrapy/issues/77", True),
    ("flaky on CI", False),
    ("", False),
    ("abc#12", False),  # not a standalone reference
])
def test_has_issue_ref(reason, ok):
    assert has_issue_ref(reason) is ok


def test_flaky_reason_keyword_and_positional():
    assert flaky_reason(pytest.mark.flaky(reason="x #1").mark) == "x #1"
    assert flaky_reason(pytest.mark.flaky("y #2").mark) == "y #2"
    assert flaky_reason(pytest.mark.flaky.mark) == ""


def test_hook_quarantines_flaky_with_issue(monkeypatch):
    monkeypatch.delenv("RUN_FLAKY", raising=False)
    flaky = _Item("t::flaky", pytest.mark.flaky(reason="timing on CI #4321").mark)
    normal = _Item("t::normal")
    conftest.pytest_collection_modifyitems(None, [flaky, normal])
    assert len(flaky.added) == 1 and flaky.added[0].name == "skip"
    assert "#4321" in flaky.added[0].kwargs["reason"]
    assert normal.added == []


def test_hook_runs_flaky_when_opted_in(monkeypatch):
    monkeypatch.setenv("RUN_FLAKY", "1")
    flaky = _Item("t::flaky", pytest.mark.flaky(reason="timing on CI #4321").mark)
    conftest.pytest_collection_modifyitems(None, [flaky])
    assert flaky.added == []


def test_hook_rejects_flaky_without_issue(monkeypatch):
    monkeypatch.delenv("RUN_FLAKY", raising=False)
    bad = _Item("t::bad", pytest.mark.flaky(reason="sometimes fails").mark)
    with pytest.raises(pytest.UsageError, match="t::bad"):
        conftest.pytest_collection_modifyitems(None, [bad])


def test_flaky_marker_is_registered(pytestconfig):
    markers = "\n".join(pytestconfig.getini("markers"))
    assert "flaky:" in markers
