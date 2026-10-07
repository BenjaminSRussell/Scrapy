"""Operator-facing seed list/add/disable with append-only audit log (#1100)."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from src.core.timeutil import utc_now_iso

logger = logging.getLogger(__name__)


def _utcnow() -> str:
    return utc_now_iso()


@dataclass
class SeedRecord:
    url: str
    status: str  # active | disabled
    added_at: str
    updated_at: str
    source: str = "cli"
    note: str = ""


class SeedRegistry:
    """JSON registry + JSONL audit, independent of Delta for operator UX/tests."""

    def __init__(self, path: Path, audit_path: Path | None = None):
        self.path = Path(path)
        self.audit_path = Path(audit_path) if audit_path else self.path.with_suffix(".audit.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, SeedRecord]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        out: dict[str, SeedRecord] = {}
        for row in raw.get("seeds", []):
            rec = SeedRecord(**row)
            out[rec.url] = rec
        return out

    def _save(self, seeds: dict[str, SeedRecord]) -> None:
        payload = {"seeds": [asdict(s) for s in sorted(seeds.values(), key=lambda r: r.url)]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _audit(self, action: str, url: str, **extra: Any) -> None:
        entry = {"ts": _utcnow(), "action": action, "url": url, **extra}
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.info("[seed_ops] %s %s", action, url)

    def list_seeds(self, *, include_disabled: bool = True) -> list[SeedRecord]:
        seeds = list(self._load().values())
        if not include_disabled:
            seeds = [s for s in seeds if s.status == "active"]
        return sorted(seeds, key=lambda s: s.url)

    def add(self, url: str, *, source: str = "cli", note: str = "", actor: str = "operator") -> SeedRecord:
        url = url.strip()
        if not url:
            raise ValueError("url required")
        seeds = self._load()
        existing = seeds.get(url)
        if existing and existing.status == "active":
            raise ValueError(f"duplicate active seed: {url}")
        now = _utcnow()
        if existing and existing.status == "disabled":
            existing.status = "active"
            existing.updated_at = now
            existing.note = note or existing.note
            seeds[url] = existing
            self._save(seeds)
            self._audit("reenable", url, actor=actor, source=source)
            return existing
        rec = SeedRecord(url=url, status="active", added_at=now, updated_at=now, source=source, note=note)
        seeds[url] = rec
        self._save(seeds)
        self._audit("add", url, actor=actor, source=source)
        return rec

    def disable(self, url: str, *, actor: str = "operator") -> SeedRecord:
        seeds = self._load()
        rec = seeds.get(url.strip())
        if rec is None:
            raise KeyError(f"unknown seed: {url}")
        if rec.status == "disabled":
            return rec
        rec.status = "disabled"
        rec.updated_at = _utcnow()
        seeds[url.strip()] = rec
        self._save(seeds)
        self._audit("disable", url.strip(), actor=actor)
        return rec

    def read_audit(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        rows = [json.loads(line) for line in lines if line.strip()]
        return rows[-limit:]
