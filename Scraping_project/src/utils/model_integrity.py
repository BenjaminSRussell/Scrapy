"""Pinned, checksum-verified ML model downloads (#486, #650).

``models.lock.json`` (repo root of Scraping_project) pins each model to a Hub
commit ``revision`` and records a digest per file: ``sha256`` for LFS files
(weights) or the git blob ``sha1`` for small files, exactly as the Hub reports
them. ``python cli.py setup`` downloads those revisions and **fails closed** if
any file is missing, has the wrong size, or has the wrong digest.

``config.yml`` ``models.stage3`` / ``models.stage4`` repeat the commit and the
weight-file sha256. Stage workers check that pin at startup. Loaders pass
``revision=`` and refuse a cached weight file that does not match.

Pin update process: ``python scripts/update_model_lock.py``, copy the new
revision and weight sha256 into ``config.yml`` and ``config.example.yml``,
review the diff, then commit (see docs/MODEL_PINS.md).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REV = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STAGE_CONFIG_KEY = {"stage3": "stage3.model_name", "stage4": "stage4.model_name"}
# Successful checks, keyed so a changed file is hashed again.
_verified: dict[tuple[str, str, str, str], tuple[int, int]] = {}
_absent_warned: set[tuple[str, str]] = set()

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


@dataclass(frozen=True)
class ModelPin:
    """``models.<stage>.revision`` plus the weight-file sha256 from config.yml (#650)."""

    stage: str
    repo_id: str
    revision: str
    sha256: str
    weight_file: str
    size: int


def huggingface_hub_cache() -> Path:
    """Same default the Hub library uses (``HF_HUB_CACHE``, else ``$HF_HOME/hub``)."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HUGGINGFACE_HUB_CACHE"):
        return Path(os.environ["HUGGINGFACE_HUB_CACHE"])
    home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    return home / "hub"


def _snapshot_dir(pin: ModelPin, cache: Path) -> Path | None:
    folder = cache / f"models--{pin.repo_id.replace('/', '--')}" / "snapshots" / pin.revision
    return folder if folder.is_dir() else None


def load_stage_pin(
    stage: str,
    *,
    config: Mapping[str, Any] | None = None,
    lock: Mapping[str, Any] | None = None,
    lock_path: Path | str = DEFAULT_LOCK,
) -> ModelPin:
    """Read ``models.<stage>.revision`` and ``sha256`` and require the lock to agree.

    ``config`` / ``lock`` override the shipped files (tests). A missing pin, a
    short revision, or a digest that is not the locked weight file raises
    :class:`ModelIntegrityError`.
    """
    if stage not in _STAGE_CONFIG_KEY:
        raise ModelIntegrityError(f"no model pin for stage {stage!r}")
    if config is None:
        from src.core.config import get_config

        section = get_config().get(f"models.{stage}")
    else:
        models = config.get("models")
        section = models.get(stage) if isinstance(models, Mapping) else None
    if not isinstance(section, Mapping):
        raise ModelIntegrityError(f"models.{stage} pin is missing (revision + sha256)")
    revision = section.get("revision")
    digest = section.get("sha256")
    if not isinstance(revision, str) or not _REV.match(revision):
        raise ModelIntegrityError(f"models.{stage}.revision must be a 40-char commit, got {revision!r}")
    if not isinstance(digest, str) or not _SHA256.match(digest):
        raise ModelIntegrityError(f"models.{stage}.sha256 must be 64 hex chars, got {digest!r}")

    loaded = lock if lock is not None else load_lock(lock_path)
    config_key = _STAGE_CONFIG_KEY[stage]
    matches = [m for m in loaded.get("models", []) if m.get("config_key") == config_key]
    if len(matches) != 1:
        raise ModelIntegrityError(f"models.lock.json must have one {config_key} entry, found {len(matches)}")
    model = matches[0]
    if model.get("revision") != revision:
        raise ModelIntegrityError(
            f"models.{stage}.revision {revision} != models.lock.json {model.get('revision')}"
        )
    weight: tuple[str, int] | None = None
    for name, spec in model.get("files", {}).items():
        if isinstance(spec, Mapping) and spec.get("sha256") == digest:
            weight = (str(name), int(spec.get("size") or 0))
            break
    if weight is None:
        raise ModelIntegrityError(
            f"models.{stage}.sha256 does not match a weight file for {model.get('repo_id')} in models.lock.json"
        )
    return ModelPin(stage, str(model["repo_id"]), revision, digest, weight[0], weight[1])


def verify_cached_weights(pin: ModelPin, *, cache: Path | None = None) -> Path | None:
    """Hash the pinned weight file when that snapshot is already cached.

    ``None`` means the revision is not on disk (nothing to hash yet). A file
    that is missing from an existing snapshot, or whose size or sha256 does
    not match, raises :class:`ModelIntegrityError`.
    """
    root = cache if cache is not None else huggingface_hub_cache()
    folder = _snapshot_dir(pin, root)
    if folder is None:
        return None
    path = folder / pin.weight_file
    if path.is_file():
        st = path.stat()
        key = (pin.repo_id, pin.revision, pin.sha256, str(path))
        if _verified.get(key) == (st.st_size, st.st_mtime_ns):
            return path
    expected: dict[str, Any] = {"sha256": pin.sha256}
    if pin.size:
        expected["size"] = pin.size
    problem = verify_file(path, expected)
    if problem:
        raise ModelIntegrityError(
            "Model integrity check FAILED (refusing to use unverified models):\n  "
            f"{pin.repo_id}@{pin.revision[:10]}: {problem}"
        )
    st = path.stat()
    _verified[(pin.repo_id, pin.revision, pin.sha256, str(path))] = (st.st_size, st.st_mtime_ns)
    return path


def verify_stage_startup(stage: str, *, cache: Path | None = None) -> str:
    """Check the config pin against the lock, and against the cache when present.

    A missing download is not a failure: the loader checks again. A mismatched
    cached file refuses startup.
    """
    pin = load_stage_pin(stage)
    found = verify_cached_weights(pin, cache=cache)
    if found is None:
        return (
            f"{stage} pin {pin.repo_id}@{pin.revision[:12]} sha256 {pin.sha256[:12]} "
            "(weights not cached; rechecked on load)"
        )
    return f"{stage} pin verified {pin.repo_id}@{pin.revision[:12]} {pin.weight_file}"


def revision_for_load(stage: str, model_name: str, *, cache: Path | None = None) -> str | None:
    """Pinned revision when ``model_name`` is that stage's repo, else ``None``.

    Verifies a cached weight file first. A different repo is not pinned by
    this stage (the caller must not send a floating revision as if it were).
    """
    pin = load_stage_pin(stage)
    if model_name != pin.repo_id:
        logger.warning(
            "models.%s pins %s but the loader asked for %s; not applying the pin",
            stage,
            pin.repo_id,
            model_name,
        )
        return None
    verify_cached_weights(pin, cache=cache)
    return pin.revision


def confirm_load(stage: str, model_name: str, *, cache: Path | None = None) -> None:
    """After a loader returns, hash the weight file if the snapshot is now cached.

    Still-absent weights are logged once. A digest mismatch raises.
    """
    pin = load_stage_pin(stage)
    if model_name != pin.repo_id:
        return
    found = verify_cached_weights(pin, cache=cache)
    if found is not None:
        return
    key = (pin.repo_id, pin.revision)
    if key in _absent_warned:
        return
    _absent_warned.add(key)
    logger.warning(
        "%s@%s: pinned weights not in the local HF cache after load; checksum not checked",
        pin.repo_id,
        pin.revision[:12],
    )
