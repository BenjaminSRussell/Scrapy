"""#426: job fail and stall webhooks. Sending uses a fake opener; nothing leaves the process."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.utils import job_alerts
from src.utils.job_alerts import (
    build_payload,
    build_test_payload,
    error_snippet,
    is_stalled,
    notify_job,
    send_test,
    send_webhook,
    webhook_url,
)
from src.utils.job_notice_prefs import NoticePrefs, store_notice_prefs

pytestmark = pytest.mark.unit

URL = "https://hooks.example.test/services/T000"
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


class _Response:
    def __init__(self, status: int = 200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    """Records each request. ``status`` is returned, or ``error`` is raised."""

    def __init__(self, status: int = 200, error: Exception | None = None):
        self.status = status
        self.error = error
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, request, timeout):
        self.calls.append((request.full_url, json.loads(request.data), timeout))
        if self.error is not None:
            raise self.error
        return _Response(self.status)


@pytest.fixture(autouse=True)
def _no_config_file(monkeypatch):
    monkeypatch.setattr(job_alerts, "_config_section", lambda: {})


def test_env_url_wins_and_must_be_plain_http_or_https():
    assert webhook_url({"ALERT_WEBHOOK_URL": URL}) == URL
    assert webhook_url({"ALERT_WEBHOOK_URL": "  " + URL + "  "}) == URL
    assert webhook_url({}) is None
    assert webhook_url({"ALERT_WEBHOOK_URL": ""}) is None
    assert webhook_url({"ALERT_WEBHOOK_URL": "ftp://hooks.example.test/x"}) is None
    assert webhook_url({"ALERT_WEBHOOK_URL": "https://user:pw@hooks.example.test/x"}) is None
    assert webhook_url({"ALERT_WEBHOOK_URL": "https://:443"}) is None


def test_config_section_is_the_fallback(monkeypatch):
    monkeypatch.setattr(job_alerts, "_config_section", lambda: {"webhook_url": URL, "stall_minutes": 5})
    assert webhook_url({}) == URL
    assert webhook_url({"ALERT_WEBHOOK_URL": "https://other.example.test/h"}) == "https://other.example.test/h"
    assert job_alerts.stall_minutes() == 5.0


def test_stall_minutes_default_and_bad_values(monkeypatch):
    assert job_alerts.stall_minutes() == job_alerts.DEFAULT_STALL_MINUTES
    monkeypatch.setattr(job_alerts, "_config_section", lambda: {"stall_minutes": "soon"})
    assert job_alerts.stall_minutes() == job_alerts.DEFAULT_STALL_MINUTES


def test_stall_fires_at_the_threshold_only():
    last = NOW - timedelta(minutes=30)
    assert is_stalled(last, NOW, 30) is True
    assert is_stalled(last + timedelta(seconds=1), NOW, 30) is False
    assert is_stalled(NOW, NOW, 0) is False  # zero turns stall alerts off


def test_error_snippet_is_one_short_line():
    assert error_snippet(None) is None
    assert error_snippet("   ") is None
    assert error_snippet(ValueError("boom\n  at line 3")) == "boom at line 3"
    long = error_snippet("x" * 2000)
    assert len(long) <= job_alerts.ERROR_SNIPPET_LIMIT


def test_fail_payload_names_job_stage_and_error():
    payload = build_payload("fail", "crawl-news", stage="stage2", last_error=RuntimeError("timeout"), when=NOW)
    assert payload["event"] == "job.failed"
    assert payload["job"] == "crawl-news"
    assert payload["stage"] == "stage2"
    assert payload["last_error"] == "timeout"
    assert payload["sent_at"] == NOW.isoformat()
    assert payload["text"] == "Job crawl-news failed at stage2: timeout"


def test_stall_payload_without_error_has_no_snippet():
    payload = build_payload("stall", "crawl-news", stage="stage1", last_error=None, when=NOW)
    assert payload["event"] == "job.stalled"
    assert payload["last_error"] is None
    assert payload["text"] == "Job crawl-news stalled at stage1"


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        build_payload("success", "j", stage="s", last_error=None, when=NOW)


def test_send_webhook_posts_json_and_reports_2xx():
    opener = _Opener(status=204)
    ok, detail = send_webhook(URL, build_test_payload(NOW), opener=opener)
    assert (ok, detail) == (True, "HTTP 204")
    url, body, timeout = opener.calls[0]
    assert url == URL
    assert body["event"] == "job.test"
    assert timeout == job_alerts.SEND_TIMEOUT


def test_send_webhook_reports_non_2xx_without_raising():
    ok, detail = send_webhook(URL, {"event": "x"}, opener=_Opener(status=500))
    assert (ok, detail) == (False, "HTTP 500")


def test_send_webhook_reports_transport_errors_by_class_only():
    ok, detail = send_webhook(URL, {"event": "x"}, opener=_Opener(error=OSError("secret host detail")))
    assert (ok, detail) == (False, "OSError")


def test_notify_job_sends_fail_with_default_prefs():
    opener = _Opener()
    delivered = notify_job(
        "fail", "crawl-news", stage="stage2", last_error="boom",
        when=NOW, env={"ALERT_WEBHOOK_URL": URL}, opener=opener,
    )
    assert delivered is True
    assert opener.calls[0][1]["event"] == "job.failed"


def test_notify_job_does_nothing_without_a_url():
    opener = _Opener()
    assert notify_job("fail", "j", stage="s", when=NOW, env={}, opener=opener) is False
    assert opener.calls == []


def test_per_job_prefs_mute_a_kind_and_quiet_hours_mute_all():
    opener = _Opener()
    env = {"ALERT_WEBHOOK_URL": URL}
    muted_stall = store_notice_prefs({}, NoticePrefs(notify_stall=False))
    assert notify_job("stall", "j", stage="s", metadata=muted_stall, when=NOW, env=env, opener=opener) is False
    quiet = store_notice_prefs({}, NoticePrefs(quiet_hours=(0, 23)))  # 12:00 local is inside
    local_noon = datetime(2026, 10, 21, 12, 0)
    assert notify_job("fail", "j", stage="s", metadata=quiet, when=local_noon, env=env, opener=opener) is False
    assert opener.calls == []


def test_notify_job_never_raises_on_a_dead_receiver():
    delivered = notify_job(
        "fail", "j", stage="s", when=NOW,
        env={"ALERT_WEBHOOK_URL": URL}, opener=_Opener(error=OSError("refused")),
    )
    assert delivered is False


def test_send_test_reports_missing_url_and_sends_when_set():
    assert send_test(env={}) == (False, "no webhook URL configured")
    opener = _Opener(status=200)
    assert send_test(env={"ALERT_WEBHOOK_URL": URL}, when=NOW, opener=opener) == (True, "HTTP 200")
    assert opener.calls[0][1]["event"] == "job.test"


def test_fake_opener_body_is_bytes_of_json():
    seen = {}

    def opener(request, timeout):
        seen["data"] = request.data
        seen["type"] = request.get_header("Content-type")
        return _Response(200)

    send_webhook(URL, {"a": 1}, opener=opener)
    assert json.loads(seen["data"]) == {"a": 1}
    assert seen["type"] == "application/json"
