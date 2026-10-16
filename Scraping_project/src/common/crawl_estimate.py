"""Ballpark crawl minutes and relative cost units before a crawl starts (#580).

Planning arithmetic for ops: a URL count and a spider mix (shares that sum to
1) become single-worker minutes and relative cost units. Nothing here is a
bill. It reads no price list, calls no API, opens no socket and starts no
spider. ``SPIDER_PROFILES`` holds placeholder rates, not measurements.
Calibrate them from a timed run before quoting a figure.

Run from ``Scraping_project/``::

    python -m src.common.crawl_estimate --urls 10000 --mix scout=0.8,javascript=0.2
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

DISCLAIMER = (
    "Non-billing. This is a ballpark planning figure, not a cloud, provider, or invoice amount. "
    "Cost units are relative (one scout worker-minute = 1) and are not currency. "
    "Rates are placeholder assumptions, not measurements."
)

MIX_SUM_TOLERANCE = 1e-6


@dataclass(frozen=True)
class SpiderProfile:
    """Planning inputs for one spider. Placeholders until calibrated."""

    urls_per_minute: float  # one worker
    worker_weight: float  # cost units per worker-minute; scout = 1.0

    def __post_init__(self) -> None:
        if not (math.isfinite(self.urls_per_minute) and self.urls_per_minute > 0):
            raise ValueError(f"urls_per_minute must be a finite number above 0, got {self.urls_per_minute!r}")
        if not (math.isfinite(self.worker_weight) and self.worker_weight >= 0):
            raise ValueError(f"worker_weight must be a finite number of 0 or more, got {self.worker_weight!r}")


# Placeholders, not measurements. The only in-repo throughput figure is
# "Current throughput: ~100-200 URLs/minute" (Scraping_project/PHASE_8_STRATEGY.md),
# an unmeasured estimate for the whole pipeline; scout takes its low end.
# javascript is a Playwright browser path, so it is slower and heavier.
SPIDER_PROFILES: dict[str, SpiderProfile] = {
    "scout": SpiderProfile(urls_per_minute=100.0, worker_weight=1.0),
    "depth": SpiderProfile(urls_per_minute=100.0, worker_weight=1.0),
    "deep_dive": SpiderProfile(urls_per_minute=100.0, worker_weight=1.0),
    "javascript": SpiderProfile(urls_per_minute=20.0, worker_weight=2.0),
}


@dataclass(frozen=True)
class SpiderShare:
    spider: str
    share: float
    urls: float
    minutes: float
    cost_units: float


@dataclass(frozen=True)
class CrawlEstimate:
    url_count: int
    minutes: float
    cost_units: float
    shares: tuple[SpiderShare, ...]
    disclaimer: str = DISCLAIMER


def validate_mix(mix: Mapping[str, float], profiles: Mapping[str, SpiderProfile] | None = None) -> dict[str, float]:
    """Check spider names and shares. Each share is finite and 0 or more; shares sum to 1."""
    known = SPIDER_PROFILES if profiles is None else profiles
    if not mix:
        raise ValueError("mix needs at least one spider, e.g. scout=1")
    checked: dict[str, float] = {}
    for spider, share in mix.items():
        if spider not in known:
            raise ValueError(f"unknown spider {spider!r}; choose from {', '.join(sorted(known))}")
        if not math.isfinite(share) or share < 0:
            raise ValueError(f"share for {spider!r} must be a number of 0 or more, got {share!r}")
        checked[spider] = float(share)
    total = sum(checked.values())
    if abs(total - 1.0) > MIX_SUM_TOLERANCE:
        raise ValueError(f"mix shares must sum to 1, got {total:g}")
    return checked


def parse_mix(text: str) -> dict[str, float]:
    """Parse ``scout=0.8,javascript=0.2``. Raises ValueError on any malformed entry."""
    mix: dict[str, float] = {}
    for entry in text.split(","):
        spider, sep, raw = entry.partition("=")
        spider = spider.strip()
        if not sep or not spider:
            raise ValueError(f"mix entry {entry.strip()!r} must look like name=share")
        if spider in mix:
            raise ValueError(f"spider {spider!r} appears more than once in the mix")
        try:
            mix[spider] = float(raw)
        except ValueError as exc:
            raise ValueError(f"share for {spider!r} is not a number: {raw.strip()!r}") from exc
    return validate_mix(mix)


def estimate_crawl(
    url_count: int,
    mix: Mapping[str, float] | None = None,
    profiles: Mapping[str, SpiderProfile] | None = None,
) -> CrawlEstimate:
    """Minutes and cost units for ``url_count`` URLs split by ``mix`` (default: all scout).

    Per spider: URLs = count x share, minutes = URLs / urls_per_minute, and
    cost units = minutes x worker_weight. Totals add the spiders together,
    which assumes one worker per spider, run one after another.
    """
    if isinstance(url_count, bool) or not isinstance(url_count, int) or url_count < 0:
        raise ValueError(f"url_count must be a whole number of 0 or more, got {url_count!r}")
    table = SPIDER_PROFILES if profiles is None else profiles
    shares = validate_mix({"scout": 1.0} if mix is None else mix, table)
    rows: list[SpiderShare] = []
    for spider, share in shares.items():
        profile = table[spider]
        urls = url_count * share
        minutes = urls / profile.urls_per_minute
        rows.append(SpiderShare(spider, share, urls, minutes, minutes * profile.worker_weight))
    return CrawlEstimate(
        url_count=url_count,
        minutes=sum(row.minutes for row in rows),
        cost_units=sum(row.cost_units for row in rows),
        shares=tuple(rows),
    )


def render(estimate: CrawlEstimate) -> str:
    lines = [
        f"Crawl estimate for {estimate.url_count:,} URLs (one worker per spider, run in series)",
        f"  {'spider':<12}{'share':>8}{'URLs':>12}{'minutes':>10}{'cost units':>12}",
    ]
    for row in estimate.shares:
        lines.append(
            f"  {row.spider:<12}{row.share * 100:>7.1f}%{row.urls:>12,.0f}"
            f"{row.minutes:>10,.0f}{row.cost_units:>12,.0f}"
        )
    lines.append(f"  {'total':<12}{'':>8}{'':>12}{estimate.minutes:>10,.0f}{estimate.cost_units:>12,.0f}")
    lines.append(f"Ballpark: ~{estimate.minutes:,.0f} minutes and ~{estimate.cost_units:,.0f} relative cost units.")
    lines.append(estimate.disclaimer)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.common.crawl_estimate",
        description="Ballpark crawl minutes and relative cost units before a crawl. Non-billing.",
    )
    parser.add_argument("--urls", type=int, required=True, help="URLs to crawl (whole number, 0 or more)")
    parser.add_argument(
        "--mix",
        default="scout=1",
        help="spider shares that sum to 1, e.g. scout=0.8,javascript=0.2 (default: scout=1)",
    )
    args = parser.parse_args(argv)
    try:
        estimate = estimate_crawl(args.urls, parse_mix(args.mix))
    except ValueError as exc:
        parser.error(str(exc))
    print(render(estimate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
