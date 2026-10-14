"""Carbon scheduling hint stays a local, default-off stub (#769)."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
import yaml

from src.stage3 import carbon_hint

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
WORKER_PATHS = (
    ROOT / "src/stage3/stage3_worker.py",
    ROOT / "src/workers/stage3_worker.py",
    ROOT / "src/stage4/stage4_worker.py",
    ROOT / "src/workers/stage4_worker.py",
)


def _forbid_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("socket opened")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    if hasattr(socket, "create_server"):
        monkeypatch.setattr(socket, "create_server", boom)


def test_stub_is_fixed_local_and_default_config_leaves_hint_off(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(carbon_hint.__file__).read_text(encoding="utf-8")
    for banned in ("socket", "urllib", "http.client", "httpx", "requests", "aiohttp"):
        assert f"import {banned}" not in source
        assert f"from {banned}" not in source

    _forbid_sockets(monkeypatch)

    assert carbon_hint.stub_carbon_reading() == "local-stub"
    assert carbon_hint.stub_carbon_reading() == carbon_hint.STUB_CARBON_READING
    assert carbon_hint.CARBON_HINT_ENABLED_KEY == "carbon_scheduling.enabled"

    assert carbon_hint.carbon_hint_enabled() is False
    assert carbon_hint.delay_window_minutes() is None

    shipped = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8"))
    assert shipped["carbon_scheduling"]["enabled"] is False
    assert shipped["carbon_scheduling"]["delay_window_minutes"] is None
    assert carbon_hint.carbon_hint_enabled(shipped) is False
    assert carbon_hint.delay_window_minutes(shipped) is None

    for disagreeing in (False, None, 0, 1, "true", "True", "yes", "on", "off", ""):
        document = {"carbon_scheduling": {"enabled": disagreeing, "delay_window_minutes": 15}}
        assert carbon_hint.carbon_hint_enabled(document) is False

    enabled = {"carbon_scheduling": {"enabled": True, "delay_window_minutes": 15}}
    assert carbon_hint.carbon_hint_enabled(enabled) is True
    assert carbon_hint.delay_window_minutes(enabled) == 15
    # The reading does not change when the hint is on, and it still does no I/O.
    assert carbon_hint.stub_carbon_reading() == "local-stub"

    for text, expected in (
        ("carbon_scheduling:\n  enabled: true\n", True),
        ("carbon_scheduling:\n  enabled: True\n", True),
        ("carbon_scheduling:\n  enabled: false\n", False),
        ("carbon_scheduling:\n  enabled: yes\n", False),
        ("carbon_scheduling:\n  enabled: on\n", False),
        ("carbon_scheduling:\n  enabled: 'true'\n", False),
        ("carbon_scheduling:\n  enabled: 1\n", False),
    ):
        assert carbon_hint.carbon_hint_enabled(carbon_hint.parse_config_text(text)) is expected

    for path in WORKER_PATHS:
        worker = path.read_text(encoding="utf-8")
        assert "carbon_hint" not in worker
        assert "carbon_scheduling" not in worker
