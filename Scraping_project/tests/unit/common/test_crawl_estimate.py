"""#580: ballpark crawl minutes and relative cost units before a start.

Planning arithmetic only. No spider runs, nothing is priced, and no network is
touched. Formula tests pass explicit rates, so tuning the placeholder table
does not move them. The name check reads the spider sources, so a renamed
spider cannot leave the mix table stale.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.common import crawl_estimate as ce

ROOT = Path(__file__).resolve().parents[3]  # Scraping_project/
RATES = {
    "scout": ce.SpiderProfile(urls_per_minute=100.0, worker_weight=1.0),
    "javascript": ce.SpiderProfile(urls_per_minute=20.0, worker_weight=2.0),
}


def test_minutes_and_units_follow_mix_times_count():
    estimate = ce.estimate_crawl(10_000, {"scout": 0.8, "javascript": 0.2}, RATES)
    rows = {row.spider: row for row in estimate.shares}
    assert rows["scout"].urls == pytest.approx(8_000)
    assert rows["scout"].minutes == pytest.approx(80)
    assert rows["javascript"].urls == pytest.approx(2_000)
    assert rows["javascript"].minutes == pytest.approx(100)
    assert estimate.minutes == pytest.approx(180)
    assert estimate.cost_units == pytest.approx(80 * 1.0 + 100 * 2.0)


def test_default_mix_is_all_scout_at_the_placeholder_rate():
    estimate = ce.estimate_crawl(1_000)
    scout = ce.SPIDER_PROFILES["scout"]
    assert [row.spider for row in estimate.shares] == ["scout"]
    assert estimate.minutes == pytest.approx(1_000 / scout.urls_per_minute)
    assert estimate.cost_units == pytest.approx(estimate.minutes * scout.worker_weight)


def test_zero_urls_costs_nothing():
    estimate = ce.estimate_crawl(0, {"scout": 1.0})
    assert estimate.minutes == 0
    assert estimate.cost_units == 0


@pytest.mark.parametrize(
    "text",
    [
        "",
        "scout",
        "scout=",
        "=0.5",
        "scout=abc",
        "scout=nan",
        "scout=inf",
        "scout=-0.5,javascript=1.5",
        "scout=0.5",
        "scout=0.8,javascript=0.4",
        "scout=0.5,scout=0.5",
        "scout=0.8,,javascript=0.2",
        "foo=1",
    ],
)
def test_parse_mix_rejects_malformed_or_unknown_input(text):
    with pytest.raises(ValueError):
        ce.parse_mix(text)


def test_parse_mix_accepts_spaces_around_names_and_values():
    assert ce.parse_mix(" scout = 0.8 , javascript=0.2 ") == {"scout": 0.8, "javascript": 0.2}


def test_unknown_spider_is_named_in_the_error():
    with pytest.raises(ValueError, match="unknown spider 'base'"):
        ce.parse_mix("base=1")


@pytest.mark.parametrize("bad", [-1, 1.5, True])
def test_url_count_must_be_a_whole_number_of_zero_or_more(bad):
    with pytest.raises(ValueError):
        ce.estimate_crawl(bad, {"scout": 1.0})


def test_profile_rates_must_be_finite_and_positive():
    with pytest.raises(ValueError):
        ce.SpiderProfile(urls_per_minute=0.0, worker_weight=1.0)
    with pytest.raises(ValueError):
        ce.SpiderProfile(urls_per_minute=10.0, worker_weight=float("nan"))


def test_render_shows_ballpark_minutes_units_and_non_billing_disclaimer():
    text = ce.render(ce.estimate_crawl(10_000, {"scout": 0.8, "javascript": 0.2}, RATES))
    assert "Ballpark: ~180 minutes and ~280 relative cost units." in text
    assert ce.DISCLAIMER in text
    assert "Non-billing." in text


def test_cli_prints_the_estimate_and_exits_zero(capsys):
    assert ce.main(["--urls", "10000", "--mix", "scout=0.8,javascript=0.2"]) == 0
    out = capsys.readouterr().out
    assert re.search(r"Ballpark: ~\d+ minutes and ~\d+ relative cost units\.", out)
    assert "Non-billing." in out


def test_cli_rejects_a_mix_that_does_not_sum_to_one(capsys):
    with pytest.raises(SystemExit) as exc:
        ce.main(["--urls", "10", "--mix", "scout=0.4"])
    assert exc.value.code == 2
    assert "must sum to 1" in capsys.readouterr().err


def test_mix_names_are_real_spider_names():
    declared: set[str] = set()
    for path in (ROOT / "src" / "stage1").rglob("*.py"):
        declared.update(re.findall(r'^    name = "([a-z_]+)"', path.read_text(encoding="utf-8"), re.M))
    assert set(ce.SPIDER_PROFILES) <= declared
