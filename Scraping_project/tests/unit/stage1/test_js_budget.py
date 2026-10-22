"""#460: the JS spider preset and operator page cap resolve env > config > preset."""

import pytest

from src.stage1.experimental.js_budget import (
    PRESETS,
    estimate_memory_mb,
    memory_warning,
    resolve_js_budget,
)

pytestmark = [pytest.mark.unit, pytest.mark.stage1]


class Cfg(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_default_preset_matches_previous_spider_settings():
    b = resolve_js_budget(Cfg(), env={})
    assert b.preset == "default"
    assert (b.concurrent_requests, b.concurrent_requests_per_domain) == (20, 10)
    assert (b.max_contexts, b.max_pages_per_context, b.max_pages) == (4, 8, 32)


def test_low_memory_preset_lowers_concurrency_and_pages():
    b = resolve_js_budget(Cfg({"stage1.js_spider_preset": "low_memory"}), env={})
    assert b.concurrent_requests < PRESETS["default"]["concurrent_requests"]
    assert b.max_pages < resolve_js_budget(Cfg(), env={}).max_pages


def test_unknown_preset_is_rejected_at_startup():
    with pytest.raises(ValueError, match="js_spider_preset"):
        resolve_js_budget(Cfg({"stage1.js_spider_preset": "huge"}), env={})


@pytest.mark.parametrize("total", [1, 2, 3, 5, 7, 12, 32, 100])
def test_operator_cap_is_never_exceeded(total):
    for preset in PRESETS:
        b = resolve_js_budget(Cfg({"stage1.js_spider_preset": preset, "stage1.js_max_concurrent_pages": total}), env={})
        assert 1 <= b.max_pages <= total
        assert b.max_contexts >= 1 and b.max_pages_per_context >= 1


def test_config_cap_splits_into_contexts_and_pages():
    b = resolve_js_budget(Cfg({"stage1.js_max_concurrent_pages": 12}), env={})
    assert (b.max_contexts, b.max_pages_per_context) == (4, 3)


def test_env_cap_beats_config_cap():
    b = resolve_js_budget(Cfg({"stage1.js_max_concurrent_pages": 12}), env={"PLAYWRIGHT_MAX_CONCURRENT_PAGES": "6"})
    assert b.max_pages == 6


def test_per_field_env_overrides_still_work_without_a_cap():
    b = resolve_js_budget(Cfg(), env={"PLAYWRIGHT_MAX_CONTEXTS": "2", "PLAYWRIGHT_MAX_PAGES_PER_CONTEXT": "5"})
    assert (b.max_contexts, b.max_pages_per_context) == (2, 5)


@pytest.mark.parametrize("bad", ["0", "-3", "many"])
def test_bad_cap_is_rejected(bad):
    with pytest.raises(ValueError, match="PLAYWRIGHT_MAX_CONCURRENT_PAGES"):
        resolve_js_budget(Cfg(), env={"PLAYWRIGHT_MAX_CONCURRENT_PAGES": bad})


def test_memory_estimate_and_warning():
    assert estimate_memory_mb(32, 100) == 3200
    assert memory_warning(32, 100, 4096) is None
    warning = memory_warning(32, 200, 4096)
    assert warning and "6,400" in warning
