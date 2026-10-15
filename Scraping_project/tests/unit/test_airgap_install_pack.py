"""Offline air-gapped install pack (#956).

The checklist must not call docker. bundle/load may call docker save/load
only, never a fetch, and never Compose.
"""
from __future__ import annotations

import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
SCRIPT = ROOT / "scripts" / "airgap_install_pack.sh"
MANIFEST = ROOT / "scripts" / "airgap_images.txt"
OVERLAY = ROOT / "docker-compose.airgap.yml"
GUIDE = ROOT / "docs" / "guides" / "AIRGAP_INSTALL.md"
MAIN = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
OBS = yaml.safe_load((ROOT / "docker-compose.observability.yml").read_text(encoding="utf-8"))


def _parse_manifest(text: str) -> dict[str, list[str]]:
    section = "required"
    out: dict[str, list[str]] = {"required": [], "optional": []}
    for raw in text.splitlines():
        line = raw.strip()
        if line == "# section: required":
            section = "required"
            continue
        if line == "# section: optional":
            section = "optional"
            continue
        if not line or line.startswith("#"):
            continue
        out[section].append(line)
    return out


MAN = _parse_manifest(MANIFEST.read_text(encoding="utf-8"))


def _third_party() -> tuple[set[str], set[str]]:
    required: set[str] = set()
    optional: set[str] = set()
    for svc in MAIN["services"].values():
        image = svc.get("image")
        if not image:
            continue
        profiles = set(svc.get("profiles") or [])
        if profiles & {"core", "monitoring"}:
            required.add(image)
        else:
            optional.add(image)
    return required, optional


def _stub(bin_dir: Path) -> None:
    path = bin_dir / "docker"
    path.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${DOCKER_LOG:?}"
if [ "${1:-}" = "image" ] && [ "${2:-}" = "inspect" ]; then
  name="${3:-}"
  case " ${MISSING_IMAGES:-} " in
    *" $name "*) exit 1 ;;
  esac
  exit 0
fi
if [ "${1:-}" = "save" ]; then
  out=""
  prev=""
  for a in "$@"; do
    if [ "$prev" = "-o" ]; then out="$a"; fi
    prev="$a"
  done
  if [ -n "$out" ]; then printf 'saved\\n' > "$out"; fi
  exit 0
fi
if [ "${1:-}" = "load" ]; then
  exit 0
fi
if [ "${1:-}" = "pull" ] || [ "${1:-}" = "push" ] || [ "${1:-}" = "login" ]; then
  echo "forbidden $1" >&2
  exit 99
fi
echo "unexpected docker $*" >&2
exit 99
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


@pytest.fixture
def docker_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir)
    log = tmp_path / "docker.log"
    log.write_text("", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
    env["DOCKER_LOG"] = str(log)
    env["MISSING_IMAGES"] = ""
    return env, log


def test_manifest_covers_compose_and_dockerfile_bases():
    required, optional = _third_party()
    req = set(MAN["required"])
    opt = set(MAN["optional"])
    assert required <= req
    assert optional <= opt
    obs_images = {svc["image"] for svc in OBS["services"].values() if "image" in svc}
    assert obs_images - req <= opt
    assert "scraping-pipeline/crawler:airgap" in req
    assert "scraping-pipeline/stage3:airgap" in req
    assert "scraping-pipeline/metrics:airgap" in opt
    assert "scraping-pipeline/kafka-delta-ingest:airgap" in opt
    for base in ("python:3.11-slim", "debian:bookworm-slim", "rust:1-slim-bookworm"):
        assert base in opt
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    stages = {m.group(1) for m in re.finditer(r"^FROM\s+\S+\s+as\s+(\S+)", dockerfile, re.I | re.M)}
    external = set(re.findall(r"^FROM\s+(\S+)", dockerfile, re.M)) - stages
    assert external <= req | opt
    names = " ".join(MAN["required"] + MAN["optional"]).lower()
    assert "stage4" not in names and "torch" not in names
    assert len(MAN["required"]) == len(set(MAN["required"]))
    assert not (req & opt)


def test_overlay_never_pulls_and_skips_stage4():
    air = yaml.safe_load(OVERLAY.read_text(encoding="utf-8"))
    text = OVERLAY.read_text(encoding="utf-8")
    assert "stage4-worker" not in air["services"]
    assert "stage4-worker:" not in text
    images = " ".join(svc["image"] for svc in air["services"].values()).lower()
    assert "stage4" not in images and "torch" not in images
    assert "REDIS_URL" not in text
    assert "ghcr.io" not in text
    assert set(air["services"]) == set(MAIN["services"]) - {"stage4-worker"}
    for name in ("scraper", "stage1-worker", "stage2-worker", "dashboard"):
        assert air["services"][name]["image"] == "scraping-pipeline/crawler:airgap"
    assert air["services"]["stage3-worker"]["image"] == "scraping-pipeline/stage3:airgap"
    for name, svc in MAIN["services"].items():
        if name == "stage4-worker":
            continue
        overlay = air["services"][name]
        assert overlay["pull_policy"] == "never"
        assert "build" not in overlay
        if "image" in svc:
            assert overlay["image"] == svc["image"]
        assert not str(overlay["image"]).startswith("ghcr.io")


def test_script_does_not_fetch_or_start_compose():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in text
    for banned in ("docker pull", "docker push", "docker login", "docker compose up"):
        assert banned not in text
    assert "docker save" in text and "docker load" in text
    assert os.access(SCRIPT, os.X_OK)


def test_checklist_prints_pack_and_does_not_call_docker(docker_env):
    env, log = docker_env
    result = _run([], env)
    assert result.returncode == 0, result.stderr
    out = result.stdout
    for needle in (
        "Checklist",
        "Image bundle steps",
        "No registry",
        "--pull never",
        "--no-build",
        "pull_policy: never",
        "docker save",
        "docker load",
        "Stage 4",
        "torch",
        "REDIS_HOST",
        "3.11",
        "3.12",
    ):
        assert needle in out, needle
    for image in MAN["required"] + MAN["optional"]:
        assert image in out
    assert "REDIS_URL" not in out
    assert log.read_text(encoding="utf-8") == ""


def test_bundle_refuses_to_fetch_when_an_image_is_missing(docker_env):
    env, log = docker_env
    env["MISSING_IMAGES"] = MAN["required"][0]
    with tempfile.TemporaryDirectory(prefix="airgap-956-", dir="/tmp") as tmp:
        out = str(Path(tmp) / "pack.tar")
        result = _run(["bundle", "--out", out], env)
    assert result.returncode != 0
    assert "not fetching" in result.stderr
    assert "does not contact a registry" in result.stderr
    recorded = log.read_text(encoding="utf-8")
    assert "pull" not in recorded and "save" not in recorded


def test_bundle_saves_required_images_only(docker_env):
    env, log = docker_env
    with tempfile.TemporaryDirectory(prefix="airgap-956-", dir="/tmp") as tmp:
        out = Path(tmp) / "pack.tar"
        result = _run(["bundle", "--out", str(out)], env)
        assert result.returncode == 0, result.stderr
        assert out.is_file() and (Path(str(out) + ".sha256")).is_file()
        assert "sha256" in result.stdout
        recorded = log.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("save ") for line in recorded)
    saved = next(line for line in recorded if line.startswith("save "))
    for image in MAN["required"]:
        assert image in saved
    for image in MAN["optional"]:
        assert image not in saved.split(" -o ")[-1] or image in MAN["required"]
    assert "pull" not in "\n".join(recorded)


def test_bundle_extras_include_optional_images(docker_env):
    env, log = docker_env
    with tempfile.TemporaryDirectory(prefix="airgap-956-", dir="/tmp") as tmp:
        out = str(Path(tmp) / "pack.tar")
        result = _run(["bundle", "--extras", "--out", out], env)
    assert result.returncode == 0, result.stderr
    saved = next(line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("save "))
    for image in MAN["optional"]:
        assert image in saved


def test_bundle_refuses_paths_outside_tmp_and_the_lake(docker_env):
    env, log = docker_env
    outside = _run(["bundle", "--out", "/etc/scraping-pipeline-airgap.tar"], env)
    assert outside.returncode == 2
    assert "outside" in outside.stderr
    lake = _run(["bundle", "--out", str(ROOT / "data" / "delta_lake" / "pack.tar")], env)
    assert lake.returncode == 2
    assert "data path" in lake.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_load_checks_sha_and_does_not_start_compose(docker_env):
    env, log = docker_env
    with tempfile.TemporaryDirectory(prefix="airgap-956-", dir="/tmp") as tmp:
        tar = Path(tmp) / "pack.tar"
        tar.write_text("saved\n", encoding="utf-8")
        side = Path(str(tar) + ".sha256")
        side.write_text("0" * 64 + "  pack.tar\n", encoding="utf-8")
        bad = _run(["load", "--tar", str(tar)], env)
        assert bad.returncode == 2
        assert "mismatch" in bad.stderr
        assert log.read_text(encoding="utf-8") == ""
        digest = subprocess.run(
            ["python3", "-c", "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())", str(tar)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        side.write_text(digest + "  pack.tar\n", encoding="utf-8")
        good = _run(["load", "--tar", str(tar)], env)
    assert good.returncode == 0, good.stderr
    assert "No registry" in good.stdout
    assert "--pull never" in good.stdout and "--no-build" in good.stdout
    assert "up -d" in good.stdout
    recorded = log.read_text(encoding="utf-8")
    assert "load -i" in recorded
    assert "pull" not in recorded
    assert "compose" not in recorded


def test_load_missing_tar_does_not_call_docker(docker_env):
    env, log = docker_env
    with tempfile.TemporaryDirectory(prefix="airgap-956-", dir="/tmp") as tmp:
        result = _run(["load", "--tar", str(Path(tmp) / "missing.tar")], env)
    assert result.returncode == 2
    assert log.read_text(encoding="utf-8") == ""


def test_guide_and_changelog_document_the_pack():
    guide = GUIDE.read_text(encoding="utf-8")
    for needle in (
        "airgap_images.txt",
        "airgap_install_pack.sh",
        "docker save",
        "docker load",
        "--pull never",
        "--no-build",
        "pull_policy: never",
        "Script checklist",
        "No registry",
        "torch",
        "scraping-pipeline/crawler:airgap",
        "REDIS_HOST",
        "python:3.11-slim",
    ):
        assert needle in guide, needle
    assert "REDIS_URL" not in guide
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "#956" in changelog
    guides = (ROOT / "docs" / "guides" / "README.md").read_text(encoding="utf-8")
    assert "AIRGAP_INSTALL.md" in guides
