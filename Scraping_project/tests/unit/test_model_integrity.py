"""Pinned model verification (#486): tampered / missing / wrong-size files fail closed."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.utils import model_integrity as mi

ROOT = Path(__file__).resolve().parents[2]
REV = "a" * 40


def _git_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


@pytest.fixture
def snapshot(tmp_path):
    d = tmp_path / "snap"
    (d / "1_Pooling").mkdir(parents=True)
    weights = b"\x00weights" * 100
    cfg = b'{"model_type": "bart"}\n'
    pool = b'{"pooling": "mean"}'
    (d / "model.safetensors").write_bytes(weights)
    (d / "config.json").write_bytes(cfg)
    (d / "1_Pooling/config.json").write_bytes(pool)
    model = {
        "name": "m",
        "repo_id": "org/m",
        "revision": REV,
        "files": {
            "model.safetensors": {"size": len(weights), "sha256": hashlib.sha256(weights).hexdigest()},
            "config.json": {"size": len(cfg), "git_sha1": _git_sha1(cfg)},
            "1_Pooling/config.json": {"size": len(pool), "git_sha1": _git_sha1(pool)},
        },
    }
    lock = tmp_path / "models.lock.json"
    lock.write_text(json.dumps({"models": [model]}))
    return d, model, lock


def _fetch_from(d):
    return lambda model, download: d


def test_git_blob_sha1_matches_git(tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"hello\n")
    assert mi.git_blob_sha1(p) == "ce013625030ba8dba906f756967f9e9ca394464a"  # `git hash-object`


def test_clean_snapshot_verifies(snapshot):
    d, _, lock = snapshot
    results = mi.setup_models(lock, fetch=_fetch_from(d))
    assert len(results) == 1 and results[0].ok


def test_tampered_weights_fail_closed(snapshot):
    d, _, lock = snapshot
    data = bytearray((d / "model.safetensors").read_bytes())
    data[0] ^= 0xFF  # same size, different content
    (d / "model.safetensors").write_bytes(bytes(data))
    with pytest.raises(mi.ModelIntegrityError, match=r"model\.safetensors: sha256"):
        mi.setup_models(lock, fetch=_fetch_from(d))


def test_tampered_small_file_and_missing_file_all_reported(snapshot):
    d, _, lock = snapshot
    (d / "config.json").write_bytes(b'{"model_type": "evil"}\n')
    (d / "1_Pooling/config.json").unlink()
    with pytest.raises(mi.ModelIntegrityError) as exc:
        mi.setup_models(lock, fetch=_fetch_from(d))
    msg = str(exc.value)
    assert "config.json" in msg and "missing config.json" in msg
    assert "refusing to use unverified models" in msg


def test_wrong_size_fails(snapshot):
    d, _, lock = snapshot
    with open(d / "model.safetensors", "ab") as f:
        f.write(b"x")
    with pytest.raises(mi.ModelIntegrityError, match="size"):
        mi.setup_models(lock, fetch=_fetch_from(d))


def test_fetch_failure_fails_closed(snapshot):
    _, _, lock = snapshot

    def boom(model, download):
        raise OSError("offline")

    with pytest.raises(mi.ModelIntegrityError, match="fetch failed: offline"):
        mi.setup_models(lock, fetch=boom, download=False)


def test_lock_requires_full_revision(tmp_path, snapshot):
    _, model, _ = snapshot
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"models": [{**model, "revision": "main"}]}))
    with pytest.raises(mi.ModelIntegrityError, match="40-char"):
        mi.load_lock(bad)


def test_only_filter(snapshot):
    d, _, lock = snapshot
    assert mi.setup_models(lock, fetch=_fetch_from(d), only=["other"]) == []
    assert len(mi.setup_models(lock, fetch=_fetch_from(d), only=["org/m"])) == 1


def test_repo_lock_is_well_formed_and_matches_config():
    lock = mi.load_lock()
    by_name = {m["name"]: m for m in lock["models"]}
    assert {"stage3_summarizer", "stage4_summarizer", "stage4_entity_embeddings"} <= set(by_name)
    for m in lock["models"]:
        for name, spec in m["files"].items():
            assert spec.get("size"), name
            assert ("sha256" in spec and len(spec["sha256"]) == 64) or len(spec.get("git_sha1", "")) == 40
        weights = [f for f in m["files"] if f.endswith((".safetensors", ".bin"))]
        assert weights and all("sha256" in m["files"][w] for w in weights)
    from src.core.config import get_config

    cfg = get_config()
    for m in lock["models"]:
        if m.get("config_key"):
            assert cfg.get(m["config_key"]) == m["repo_id"], m["config_key"]
    assert mi.pinned_revision("facebook/bart-large-cnn") == by_name["stage4_summarizer"]["revision"]
    assert mi.pinned_revision("nope/nope") is None


def test_cli_setup_fails_closed_on_tamper(snapshot, tmp_path):
    d, model, _ = snapshot
    # Point the lock at a cache-only check of a repo that is not cached -> exit 1, clear error.
    lock = tmp_path / "offline.json"
    lock.write_text(json.dumps({"models": [{**model, "repo_id": "org/definitely-not-cached-486"}]}))
    env = {"HF_HUB_OFFLINE": "1", "HF_HOME": str(tmp_path / "hf"), "PATH": "/usr/bin:/bin"}
    r = subprocess.run(
        [sys.executable, "cli.py", "setup", "--verify-only", "--lock", str(lock)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 1, r.stderr[-2000:]
    assert "Model integrity check FAILED" in r.stderr
