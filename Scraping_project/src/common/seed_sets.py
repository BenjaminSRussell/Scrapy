"""Named, reusable seed sets for operator-run jobs (#431).

A seed set is a named list of URLs an operator can create once, import from a
CSV under ``data/raw/``, and attach to a scrape job (``cli.py scrapy --seed-set``).
The registry is a JSON file, separate from the per-URL ``SeedRegistry``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from src.core.timeutil import utc_now_iso
from src.utils.validation import url_rejection_reason

RAW_SEED_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass
class SeedSet:
    name: str
    urls: list[str]
    created_at: str
    updated_at: str
    source: str = "manual"
    last_used_at: str | None = None

    @property
    def url_count(self) -> int:
        return len(self.urls)


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid seed set name {name!r}: use 1-64 letters, digits, '_', '.', or '-'")
    return name


def _clean_urls(urls: list[str]) -> tuple[list[str], int]:
    """Strip, drop non-crawlable URLs, and dedupe in order. Returns (urls, rejected)."""
    kept: dict[str, None] = {}
    rejected = 0
    for raw in urls:
        url = raw.strip() if isinstance(raw, str) else raw
        if not url:
            continue
        if url_rejection_reason(url) is not None:
            rejected += 1
            continue
        kept.setdefault(url)
    return list(kept), rejected


class SeedSetRegistry:
    """JSON-backed registry of named seed sets."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, SeedSet]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return {row["name"]: SeedSet(**row) for row in raw.get("seed_sets", [])}

    def _save(self, sets: dict[str, SeedSet]) -> None:
        payload = {"seed_sets": [asdict(s) for s in sorted(sets.values(), key=lambda s: s.name)]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def list_sets(self) -> list[SeedSet]:
        return sorted(self._load().values(), key=lambda s: s.name)

    def get(self, name: str) -> SeedSet:
        seed_set = self._load().get(name)
        if seed_set is None:
            raise KeyError(f"unknown seed set: {name}")
        return seed_set

    def create(self, name: str, urls: list[str], *, source: str = "manual") -> tuple[SeedSet, int]:
        """Create a set. Returns (set, rejected_url_count)."""
        name = _validate_name(name)
        sets = self._load()
        if name in sets:
            raise ValueError(f"seed set already exists: {name}")
        cleaned, rejected = _clean_urls(urls)
        if not cleaned:
            raise ValueError(f"seed set {name} has no valid http(s) URLs")
        now = utc_now_iso()
        seed_set = SeedSet(name=name, urls=cleaned, created_at=now, updated_at=now, source=source)
        sets[name] = seed_set
        self._save(sets)
        return seed_set, rejected

    def import_csv(self, name: str, csv_path: str | Path) -> tuple[SeedSet, int]:
        """Create a set from a CSV under ``data/raw/`` (one URL per line, header optional)."""
        path = Path(csv_path).resolve()
        if not path.is_relative_to(RAW_SEED_DIR.resolve()):
            raise ValueError(f"seed CSV must be under {RAW_SEED_DIR}: {csv_path}")
        if not path.is_file():
            raise FileNotFoundError(f"seed CSV not found: {csv_path}")
        lines = path.read_text(encoding="utf-8").splitlines()
        urls = [line.split(",", 1)[0] for line in lines]
        return self.create(name, urls, source=f"csv:{path.name}")

    def delete(self, name: str) -> SeedSet:
        sets = self._load()
        seed_set = sets.pop(name, None)
        if seed_set is None:
            raise KeyError(f"unknown seed set: {name}")
        self._save(sets)
        return seed_set

    def mark_used(self, name: str) -> SeedSet:
        sets = self._load()
        seed_set = sets.get(name)
        if seed_set is None:
            raise KeyError(f"unknown seed set: {name}")
        seed_set.last_used_at = utc_now_iso()
        self._save(sets)
        return seed_set
