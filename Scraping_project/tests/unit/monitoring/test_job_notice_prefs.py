"""#487: per-job fail/success/stall toggles and optional quiet hours.

The filter is pure. Defaults keep today's notices: fail on, success off,
stall on, quiet hours empty. Preferences are the metadata shape, not a table.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from src.utils.job_notice_prefs import (
    NoticePrefs,
    allow_notice,
    allow_notice_from_metadata,
    default_notice_prefs,
    hour_in_quiet_window,
    local_hour_of,
    notice_prefs_from_metadata,
    store_notice_prefs,
)

pytestmark = pytest.mark.unit


def test_defaults_keep_fail_and_stall_and_drop_success():
    prefs = default_notice_prefs()
    assert prefs.notify_fail is True
    assert prefs.notify_success is False
    assert prefs.notify_stall is True
    assert prefs.quiet_hours is None
    for hour in range(24):
        assert allow_notice("fail", prefs, hour) is True
        assert allow_notice("stall", prefs, hour) is True
        assert allow_notice("success", prefs, hour) is False


def test_missing_metadata_uses_the_same_defaults():
    assert notice_prefs_from_metadata(None) == default_notice_prefs()
    assert notice_prefs_from_metadata({}) == default_notice_prefs()
    assert allow_notice_from_metadata("success", None, 12) is False
    assert allow_notice_from_metadata("fail", {}, 3) is True
    assert allow_notice_from_metadata("stall", {}, 3) is True


def test_success_off_drops_success_even_outside_quiet_hours():
    prefs = NoticePrefs(notify_fail=True, notify_success=False, notify_stall=True, quiet_hours=(22, 7))
    assert allow_notice("success", prefs, 12) is False
    assert allow_notice("success", prefs, 21) is False


def test_success_on_is_delivered_outside_the_window():
    prefs = NoticePrefs(notify_success=True, quiet_hours=(22, 7))
    assert allow_notice("success", prefs, 12) is True
    assert allow_notice("success", prefs, 7) is True
    assert allow_notice("success", prefs, 21) is True


def test_fail_and_stall_stay_when_toggles_are_on():
    prefs = NoticePrefs(notify_fail=True, notify_success=False, notify_stall=True, quiet_hours=(9, 17))
    for hour in (0, 8, 17, 23):
        assert allow_notice("fail", prefs, hour) is True
        assert allow_notice("stall", prefs, hour) is True


def test_toggles_off_drop_that_event_outside_quiet_hours():
    prefs = NoticePrefs(notify_fail=False, notify_success=True, notify_stall=False, quiet_hours=None)
    assert allow_notice("fail", prefs, 10) is False
    assert allow_notice("stall", prefs, 10) is False
    assert allow_notice("success", prefs, 10) is True


def test_quiet_hours_drop_every_kind_inside_a_wrapping_window():
    prefs = NoticePrefs(notify_fail=True, notify_success=True, notify_stall=True, quiet_hours=(22, 7))
    inside = (22, 23, 0, 1, 6)
    outside = (7, 12, 21)
    for hour in inside:
        assert hour_in_quiet_window(hour, prefs.quiet_hours) is True
        assert allow_notice("fail", prefs, hour) is False
        assert allow_notice("success", prefs, hour) is False
        assert allow_notice("stall", prefs, hour) is False
    for hour in outside:
        assert hour_in_quiet_window(hour, prefs.quiet_hours) is False
        assert allow_notice("fail", prefs, hour) is True
        assert allow_notice("stall", prefs, hour) is True
        assert allow_notice("success", prefs, hour) is True


def test_daytime_window_is_half_open():
    window = (9, 17)
    assert hour_in_quiet_window(9, window) is True
    assert hour_in_quiet_window(16, window) is True
    assert hour_in_quiet_window(17, window) is False
    assert hour_in_quiet_window(8, window) is False


def test_empty_quiet_hours_match_no_hour():
    assert hour_in_quiet_window(0, None) is False
    assert hour_in_quiet_window(22, (0, 0)) is False
    assert hour_in_quiet_window(0, (5, 5)) is False
    prefs = NoticePrefs(quiet_hours=(4, 4))
    assert allow_notice("fail", prefs, 4) is True
    assert allow_notice("stall", prefs, 4) is True
    assert allow_notice("success", prefs, 4) is False


def test_metadata_shape_round_trips_and_keeps_other_keys():
    metadata = {"job_id": "job-9", "owner": "ops"}
    prefs = NoticePrefs(notify_fail=False, notify_success=True, notify_stall=True, quiet_hours=(22, 6))
    stored = store_notice_prefs(metadata, prefs)
    assert stored is metadata
    assert metadata["job_id"] == "job-9"
    assert metadata["owner"] == "ops"
    assert metadata["notify_fail"] is False
    assert metadata["notify_success"] is True
    assert metadata["notify_stall"] is True
    assert metadata["quiet_hours"] == [22, 6]
    assert notice_prefs_from_metadata(metadata) == prefs
    assert allow_notice_from_metadata("fail", metadata, 12) is False
    assert allow_notice_from_metadata("success", metadata, 12) is True
    assert allow_notice_from_metadata("stall", metadata, 23) is False
    assert allow_notice_from_metadata("stall", metadata, 6) is True


def test_store_defaults_writes_todays_notices():
    metadata: dict = {"job_id": "job-1"}
    store_notice_prefs(metadata)
    assert notice_prefs_from_metadata(metadata) == default_notice_prefs()
    assert metadata["quiet_hours"] is None
    assert metadata["notify_success"] is False


def test_partial_metadata_fills_only_the_missing_toggles():
    prefs = notice_prefs_from_metadata({"notify_success": True, "quiet_hours": []})
    assert prefs == NoticePrefs(notify_fail=True, notify_success=True, notify_stall=True, quiet_hours=None)


def test_quiet_hours_null_and_tuple_are_accepted():
    assert notice_prefs_from_metadata({"quiet_hours": None}).quiet_hours is None
    assert notice_prefs_from_metadata({"quiet_hours": (1, 2)}).quiet_hours == (1, 2)


def test_naive_datetime_hour_is_already_local():
    assert local_hour_of(datetime(2026, 10, 14, 22, 15)) == 22


def test_aware_datetime_uses_the_local_zone():
    when = datetime(2026, 1, 15, 22, 5, tzinfo=UTC)
    assert local_hour_of(when) == when.astimezone().hour
    # A fixed offset is not "local" until converted. 03:00 at UTC-5 is 08:00 UTC.
    offset = timezone(timedelta(hours=-5))
    shifted = datetime(2026, 6, 1, 3, 0, tzinfo=offset)
    assert local_hour_of(shifted) == shifted.astimezone().hour


@pytest.mark.parametrize(
    "metadata",
    [
        {"notify_fail": 1},
        {"notify_success": "yes"},
        {"notify_stall": None},
        {"quiet_hours": "22-7"},
        {"quiet_hours": [22]},
        {"quiet_hours": [22, 7, 8]},
        {"quiet_hours": [22, True]},
        {"quiet_hours": [-1, 4]},
        {"quiet_hours": [0, 24]},
        {"quiet_hours": {"start": 22, "end": 7}},
    ],
)
def test_bad_metadata_is_rejected(metadata):
    with pytest.raises(ValueError):
        notice_prefs_from_metadata(metadata)


@pytest.mark.parametrize("hour", [-1, 24, True, 1.5, "9"])
def test_bad_local_hour_is_rejected(hour):
    with pytest.raises(ValueError):
        allow_notice("fail", default_notice_prefs(), hour)  # type: ignore[arg-type]


def test_unknown_kind_and_non_mapping_are_rejected():
    with pytest.raises(ValueError):
        allow_notice("webhook", default_notice_prefs(), 1)
    with pytest.raises(ValueError):
        notice_prefs_from_metadata(["notify_fail"])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store_notice_prefs([])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        allow_notice("fail", {"notify_fail": True}, 1)  # type: ignore[arg-type]
