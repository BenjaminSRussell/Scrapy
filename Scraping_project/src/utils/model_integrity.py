"""Pinned, checksum-verified ML model downloads (#486).

``models.lock.json`` (repo root of Scraping_project) pins each model to a Hub
commit ``revision`` and records a digest per file: ``sha256`` for LFS files
(weights) or the git blob ``sha1`` for small files, exactly as the Hub reports
them. ``python cli.py setup`` downloads those revisions and **fails closed** if
any file is missing, has the wrong size, or has the wrong digest.

Pin update process: ``python scripts/update_model_lock.py``, review the diff,
then commit it (see docs/MODEL_PINS.md).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_LOCK = Path(__file__).resolve().parents[2] / "models.lock.json"
_CHUNK = 1 << 20


class ModelIntegrityError(RuntimeError):
    """A pinned model file is missing or does not match models.lock.json."""


@dataclass
class ModelCheck:
    repo_id: str
    revision: str
    path: Path | None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def load_lock(path: Path | str = DEFAULT_LOCK) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(Path(path).read_text())
    for m in data.get("models", []):
        if not m.get("revision") or len(m["revision"]) != 40:
            raise ModelIntegrityError(f"{m.get('repo_id')}: lock must pin a full 40-char commit revision")
        if not m.get("files"):
            raise ModelIntegrityError(f"{m.get('repo_id')}: lock lists no files")
    return data


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha1(path: Path) -> str:
    """Git object id of a file (what the Hub reports as ``blobId`` for non-LFS files)."""
    size = path.stat().st_size
    h = hashlib.sha1(usedforsecurity=False)
    h.update(f"blob {size}\0".encode())
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_file(path: Path, expected: dict[str, Any]) -> str | None:
    """None when ``path`` matches ``expected``; else a human-readable problem."""
    if not path.is_file():
        return f"missing {path.name}"
    size = path.stat().st_size
    if expected.get("size") is not None and size != int(expected["size"]):
        return f"{path.name}: size {size} != pinned {expected['size']}"
    if "sha256" in expected:
        got = sha256_file(path)
        if got != expected["sha256"]:
            return f"{path.name}: sha256 {got[:12]}… != pinned {expected['sha256'][:12]}…"
    elif "git_sha1" in expected:
        got = git_blob_sha1(path)
        if got != expected["git_sha1"]:
            return f"{path.name}: git sha1 {got[:12]}… != pinned {expected['git_sha1'][:12]}…"
    else:
        return f"{path.name}: lock entry has no digest"
    return None


def verify_model_dir(model: dict[str, Any], model_dir: Path) -> ModelCheck:
    check = ModelCheck(model["repo_id"], model["revision"], model_dir)
    for rel, expected in sorted(model["files"].items()):
        problem = verify_file(model_dir / rel, expected)
        if problem:
            check.problems.append(problem)
    return check


Downloader = Callable[[dict[str, Any], bool], Path]


def hf_snapshot(model: dict[str, Any], download: bool) -> Path:
    """Local snapshot dir for the pinned revision (downloads it unless ``download`` is False)."""
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            repo_id=model["repo_id"],
            revision=model["revision"],
            allow_patterns=sorted(model["files"]),
            local_files_only=not download,
        )
    )


def setup_models(
    lock_path: Path | str = DEFAULT_LOCK,
    *,
    download: bool = True,
    only: list[str] | None = None,
    fetch: Downloader = hf_snapshot,
) -> list[ModelCheck]:
    """Download (optionally) and verify every pinned model; raise on any mismatch."""
    lock = load_lock(lock_path)
    results: list[ModelCheck] = []
    for model in lock["models"]:
        if only and model["name"] not in only and model["repo_id"] not in only:
            continue
        try:
            path = fetch(model, download)
        except Exception as exc:  # network, auth, not in cache
            results.append(ModelCheck(model["repo_id"], model["revision"], None, [f"fetch failed: {exc}"]))
            continue
        results.append(verify_model_dir(model, path))
    bad = [r for r in results if not r.ok]
    if bad:
        lines = [f"{r.repo_id}@{r.revision[:10]}: " + "; ".join(r.problems) for r in bad]
        raise ModelIntegrityError(
            "Model integrity check FAILED (refusing to use unverified models):\n  " + "\n  ".join(lines)
        )
    return results


def pinned_revision(repo_id: str, lock_path: Path | str = DEFAULT_LOCK) -> str | None:
    """Pinned revision for ``repo_id`` (for loaders that accept ``revision=``)."""
    try:
        for m in load_lock(lock_path)["models"]:
            if m["repo_id"] == repo_id:
                return str(m["revision"])
    except (OSError, ValueError, ModelIntegrityError):
        return None
    return None
