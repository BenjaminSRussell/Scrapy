"""TTL garbage-collection for on-disk crawl artifacts (logs/cache/temp/raw).

``plan_gc`` never deletes; ``apply_gc`` requires an explicit call after review.
Used by ``python -m cli data gc`` / scrapy-ops data gc.
"""
from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

# Default roots relative to Scraping_project/
DEFAULT_ROOTS = ("data/logs", "data/cache", "data/temp", "logs", "data/raw/tmp")


@dataclass
class GcCandidate:
    path: str
    size_bytes: int
    mtime_epoch: float
    age_days: float


@dataclass
class GcReport:
    root: str
    ttl_days: float
    candidates: list[GcCandidate]
    total_files: int
    total_bytes: int
    deleted_files: int = 0
    deleted_bytes: int = 0
    dry_run: bool = True
    errors: list[str] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _iter_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for p in root.rglob("*"):
            if p.is_file():
                yield p


def plan_gc(
    *,
    base_dir: Path,
    ttl_days: float,
    roots: Iterable[str] | None = None,
    now: float | None = None,
) -> GcReport:
    """List files under artifact roots older than ttl_days. Never deletes."""
    now = time.time() if now is None else now
    ttl_seconds = max(float(ttl_days), 0.0) * 86400.0
    root_names = list(roots) if roots is not None else list(DEFAULT_ROOTS)
    abs_roots = [(base_dir / r).resolve() for r in root_names]
    candidates: list[GcCandidate] = []
    for path in _iter_files(abs_roots):
        try:
            st = path.stat()
        except OSError:
            continue
        age = now - st.st_mtime
        if age >= ttl_seconds:
            candidates.append(
                GcCandidate(
                    path=str(path),
                    size_bytes=int(st.st_size),
                    mtime_epoch=float(st.st_mtime),
                    age_days=round(age / 86400.0, 3),
                )
            )
    total_bytes = sum(c.size_bytes for c in candidates)
    return GcReport(
        root=str(base_dir),
        ttl_days=float(ttl_days),
        candidates=candidates,
        total_files=len(candidates),
        total_bytes=total_bytes,
        dry_run=True,
        errors=[],
    )


def apply_gc(report: GcReport) -> GcReport:
    """Delete files listed in a prior plan_gc report. Mutates a copy of report."""
    deleted_files = 0
    deleted_bytes = 0
    errors: list[str] = list(report.errors or [])
    for c in report.candidates:
        path = Path(c.path)
        try:
            if path.is_file():
                path.unlink()
                deleted_files += 1
                deleted_bytes += c.size_bytes
                logger.info("[crawl_artifact_gc] deleted %s (%d bytes)", c.path, c.size_bytes)
        except OSError as exc:
            errors.append(f"{c.path}: {exc}")
            logger.warning("[crawl_artifact_gc] failed %s: %s", c.path, exc)
    return GcReport(
        root=report.root,
        ttl_days=report.ttl_days,
        candidates=report.candidates,
        total_files=report.total_files,
        total_bytes=report.total_bytes,
        deleted_files=deleted_files,
        deleted_bytes=deleted_bytes,
        dry_run=False,
        errors=errors,
    )


def format_report(report: GcReport, *, verbose: bool = False) -> str:
    mode = "DRY-RUN" if report.dry_run else "APPLY"
    lines = [
        f"[{mode}] ttl_days={report.ttl_days} root={report.root}",
        f"  candidates={report.total_files} bytes={report.total_bytes}",
    ]
    if not report.dry_run:
        lines.append(f"  deleted_files={report.deleted_files} deleted_bytes={report.deleted_bytes}")
    if report.errors:
        lines.append(f"  errors={len(report.errors)}")
    if verbose:
        for c in report.candidates[:50]:
            lines.append(f"  - {c.path} age_days={c.age_days} size={c.size_bytes}")
        if len(report.candidates) > 50:
            lines.append(f"  ... and {len(report.candidates) - 50} more")
    return "\n".join(lines)
