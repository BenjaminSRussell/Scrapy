"""Free-space and inode watermarks for the lake volume (#561).

A full volume makes Delta writes fail with ENOSPC while the process stays up, so
health checks still pass and batches are lost. Writers ask this module before they
touch disk, and readiness uses the same answer::

    from src.utils.disk_guard import check_volume
    status = check_volume("./data/delta_lake", min_free_percent=5, min_free_inodes_percent=5)
    if status.shortfall:
        ...  # refuse the write; status.shortfall says why

Percentages are of the volume that holds ``path`` and are compared with the
space available to non-root users (``f_bavail`` / ``f_favail``), which is what
ENOSPC is raised against. A volume that cannot be stat'ed is treated as short.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VolumeStatus:
    free_percent: float
    free_inodes_percent: float
    shortfall: str | None  # why writes are refused; None when there is room


def _existing_ancestor(path: str | os.PathLike[str]) -> Path:
    """The path itself, or its nearest parent that exists (statvfs needs one)."""
    candidate = Path(path).resolve()
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    return candidate


def check_volume(
    path: str | os.PathLike[str],
    min_free_percent: float = 5.0,
    min_free_inodes_percent: float = 5.0,
) -> VolumeStatus:
    """Free space and inodes on the volume holding ``path``, against the watermarks."""
    try:
        st = os.statvfs(_existing_ancestor(path))
    except OSError as e:
        return VolumeStatus(0.0, 0.0, f"cannot stat volume for {path}: {e}")
    free = 100.0 * st.f_bavail / st.f_blocks if st.f_blocks else 100.0
    inodes = 100.0 * st.f_favail / st.f_files if st.f_files else 100.0
    shortfall = None
    if free < min_free_percent:
        shortfall = f"free space {free:.1f}% below watermark {min_free_percent:g}%"
    elif inodes < min_free_inodes_percent:
        shortfall = f"free inodes {inodes:.1f}% below watermark {min_free_inodes_percent:g}%"
    return VolumeStatus(free, inodes, shortfall)
