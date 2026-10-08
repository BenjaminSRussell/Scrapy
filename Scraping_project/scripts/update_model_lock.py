#!/usr/bin/env python3
"""Regenerate models.lock.json from the Hugging Face Hub API (#486).

Pins every model to a commit ``revision`` and records a digest per file:
``sha256`` for LFS files (weights) and the git blob ``sha1`` for small files,
both as published by the Hub. Review the diff before committing a new lock.

    python scripts/update_model_lock.py            # re-pin to the current revisions
    python scripts/update_model_lock.py --check    # exit 1 if the lock is stale

Only stdlib; needs network access to huggingface.co.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

LOCK = Path(__file__).resolve().parents[1] / "models.lock.json"
API = "https://huggingface.co/api/models/{repo}/revision/{rev}?blobs=true"


def fetch(repo: str, rev: str = "main") -> dict:
    req = urllib.request.Request(API.format(repo=repo, rev=rev), headers={"User-Agent": "scrapy-model-lock"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - fixed https host
        return json.load(resp)


def pin(entry: dict, rev: str = "main") -> dict:
    meta = fetch(entry["repo_id"], rev)
    siblings = {s["rfilename"]: s for s in meta.get("siblings", [])}
    files = {}
    for name in sorted(entry["files"]):
        s = siblings.get(name)
        if s is None:
            raise SystemExit(f"{entry['repo_id']}: {name} not in revision {meta.get('sha')}")
        lfs = s.get("lfs") or {}
        files[name] = (
            {"size": lfs.get("size", s.get("size")), "sha256": lfs["sha256"]}
            if lfs.get("sha256")
            else {"size": s.get("size"), "git_sha1": s["blobId"]}
        )
    return {**entry, "revision": meta["sha"], "files": files}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if any model moved past its pinned revision")
    args = ap.parse_args()
    lock = json.loads(LOCK.read_text())
    new = dict(lock)
    new["models"] = [pin(m) for m in lock["models"]]
    if args.check:
        stale = [m["repo_id"] for m, n in zip(lock["models"], new["models"], strict=True) if m["revision"] != n["revision"]]
        if stale:
            print("Upstream moved: " + ", ".join(stale))
            return 1
        print("models.lock.json is current")
        return 0
    LOCK.write_text(json.dumps(new, indent=2) + "\n")
    print(f"wrote {LOCK}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
