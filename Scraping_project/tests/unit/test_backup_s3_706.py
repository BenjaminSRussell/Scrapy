"""#706: BACKUP_S3_URI uploads and restores through aws or mc.

The Compose `backup` profile publishes MinIO on 127.0.0.1:9000. These tests do
not start Docker. A fake aws/mc on PATH checks the MinIO client contract
(path-style config, endpoint, credentials from the environment, temp mc config)
and stores the objects so restore can read them back.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")

ENDPOINT = "http://127.0.0.1:9000"
URI = "s3://scrapy-backups/pipeline"
ACCESS = "AKIATEST706"
SECRET = "s3cret-706-ZZ"
AWS_FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

def fail(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)

args = sys.argv[1:]
endpoint = None
cleaned = []
i = 0
while i < len(args):
    if args[i] == "--endpoint-url":
        endpoint = args[i + 1]
        i += 2
        continue
    if args[i] == "--only-show-errors":
        i += 1
        continue
    cleaned.append(args[i])
    i += 1

root = Path(os.environ["FAKE_S3_ROOT"])
root.mkdir(parents=True, exist_ok=True)
secret = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
access = os.environ.get("AWS_ACCESS_KEY_ID", "")
blob = " ".join(sys.argv)
if secret and secret in blob:
    fail("secret in argv", 7)
if access and access in blob:
    fail("access key in argv", 7)
expect = os.environ.get("EXPECT_ENDPOINT", "")
if expect and endpoint != expect:
    fail("endpoint %r != %r" % (endpoint, expect), 9)
if endpoint:
    cfg_path = os.environ.get("AWS_CONFIG_FILE", "")
    if not cfg_path or "scrapy-s3" not in cfg_path:
        fail("AWS_CONFIG_FILE %r" % cfg_path, 9)
    text = Path(cfg_path).read_text()
    if "addressing_style = path" not in text:
        fail("path style missing", 9)
    if os.environ.get("AWS_REQUEST_CHECKSUM_CALCULATION") != "when_required":
        fail("checksum mode", 9)
    if os.environ.get("AWS_DEFAULT_REGION") != "us-east-1":
        fail("region %r" % os.environ.get("AWS_DEFAULT_REGION"), 9)

def record(op, detail):
    with (root / "calls.log").open("a") as fh:
        fh.write(json.dumps({"op": op, "detail": detail, "endpoint": endpoint}) + "\n")

def bucket_dir(name):
    return root / "buckets" / name

def parse_s3(url):
    if not url.startswith("s3://"):
        fail("not s3 %s" % url, 4)
    rest = url[5:]
    bucket, _, key = rest.partition("/")
    return bucket, key

if cleaned[:2] == ["s3api", "head-bucket"]:
    b = cleaned[cleaned.index("--bucket") + 1]
    record("head-bucket", b)
    sys.exit(0 if bucket_dir(b).is_dir() else 1)
if cleaned[:2] == ["s3", "mb"]:
    b, key = parse_s3(cleaned[2])
    if key:
        fail("mb key", 4)
    bucket_dir(b).mkdir(parents=True, exist_ok=True)
    record("mb", b)
    sys.exit(0)
if cleaned[:2] == ["s3api", "head-object"]:
    b = cleaned[cleaned.index("--bucket") + 1]
    k = cleaned[cleaned.index("--key") + 1]
    record("head-object", "%s/%s" % (b, k))
    sys.exit(0 if (bucket_dir(b) / k).is_file() else 1)
if cleaned[:2] == ["s3", "cp"]:
    src, dest = cleaned[2], cleaned[3]
    if src.startswith("s3://"):
        b, k = parse_s3(src)
        target = Path(dest)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((bucket_dir(b) / k).read_bytes())
        record("download", "%s/%s" % (b, k))
    else:
        b, k = parse_s3(dest)
        dest_path = bucket_dir(b) / k
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(Path(src).read_bytes())
        record("upload", "%s/%s" % (b, k))
    sys.exit(0)
fail("unknown " + " ".join(cleaned), 5)
'''

MC_FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

def fail(msg, code):
    sys.stderr.write(msg + "\n")
    sys.exit(code)

args = sys.argv[1:]
root = Path(os.environ["FAKE_S3_ROOT"])
root.mkdir(parents=True, exist_ok=True)
cfg = os.environ.get("MC_CONFIG_DIR", "")
if not cfg or "scrapy-s3" not in cfg:
    fail("MC_CONFIG_DIR %r" % cfg, 6)
home_mc = str(Path(os.environ.get("HOME", "")) / ".mc")
if os.path.abspath(cfg) == os.path.abspath(home_mc):
    fail("wrote user mc config", 6)

def record(op, detail):
    with (root / "calls.log").open("a") as fh:
        fh.write(json.dumps({"op": op, "detail": detail}) + "\n")

def split_target(target):
    parts = target.split("/", 2)
    if len(parts) < 2 or parts[0] != "scrapybackup":
        fail("target %s" % target, 4)
    bucket = parts[1]
    key = parts[2] if len(parts) == 3 else ""
    return bucket, key

if args[:2] == ["alias", "set"]:
    name, url, access, secret = args[2:6]
    if name != "scrapybackup":
        fail("alias", 4)
    expect = os.environ.get("EXPECT_ENDPOINT", "")
    if expect and url != expect:
        fail("endpoint", 9)
    if access != os.environ.get("AWS_ACCESS_KEY_ID") or secret != os.environ.get("AWS_SECRET_ACCESS_KEY"):
        fail("credential mismatch", 7)
    record("alias", url)
    sys.exit(0)
if args[0] == "stat":
    bucket, key = split_target(args[1])
    path = root / "buckets" / bucket
    if key:
        path = path / key
    record("stat", "%s/%s" % (bucket, key))
    sys.exit(0 if path.exists() else 1)
if args[0] == "mb":
    bucket, key = split_target(args[-1])
    if key:
        fail("mb key", 4)
    (root / "buckets" / bucket).mkdir(parents=True, exist_ok=True)
    record("mb", bucket)
    sys.exit(0)
if args[0] == "cp":
    src, dest = args[1], args[2]
    if src.startswith("scrapybackup/"):
        bucket, key = split_target(src)
        data = (root / "buckets" / bucket / key).read_bytes()
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(data)
        record("download", "%s/%s" % (bucket, key))
    else:
        bucket, key = split_target(dest)
        dest_path = root / "buckets" / bucket / key
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(Path(src).read_bytes())
        record("upload", "%s/%s" % (bucket, key))
    sys.exit(0)
fail("unknown " + " ".join(args), 5)
'''


def _tool_dir(tmp_path: Path, extra: dict[str, str]) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in (
        "python3",
        "tar",
        "date",
        "ls",
        "find",
        "mkdir",
        "rm",
        "cp",
        "mktemp",
        "basename",
        "dirname",
        "cat",
        "bash",
    ):
        src = shutil.which(name)
        assert src, name
        (bindir / name).symlink_to(src)
    for name, body in extra.items():
        path = bindir / name
        path.write_text(body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return bindir


def _env(tmp_path: Path, bindir: Path, store: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_PROFILE",
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
        "AWS_ENDPOINT_URL",
        "BACKUP_S3_URI",
        "BACKUP_S3_ENDPOINT",
        "BACKUP_S3_CLIENT",
        "MC_CONFIG_DIR",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
    ):
        env.pop(key, None)
    env["PATH"] = str(bindir)
    env["HOME"] = str(tmp_path / "home")
    env["TMPDIR"] = str(tmp_path / "tmp")
    env["FAKE_S3_ROOT"] = str(store)
    env["EXPECT_ENDPOINT"] = ENDPOINT
    Path(env["HOME"]).mkdir()
    Path(env["TMPDIR"]).mkdir()
    store.mkdir()
    return env


def _run(script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(ROOT / "scripts" / script)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _calls(store: Path) -> list[dict]:
    log = store / "calls.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def _lake(work: Path) -> tuple[Path, Path]:
    src = work / "lake"
    (src / "sample_table").mkdir(parents=True)
    (src / "sample_table" / "part.txt").write_text("marker-706\n")
    sql = work / "sample.sql"
    sql.write_text("SELECT 1;\n")
    return src, sql


@pytest.fixture
def work():
    # guard_path allows /tmp (or the worktree) only. pytest's tmp_path is not.
    path = Path(tempfile.mkdtemp(prefix="scrapy-706-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_dry_run_skips_s3_and_does_not_invoke_a_client(tmp_path):
    store = tmp_path / "s3"
    bindir = _tool_dir(tmp_path, {"aws": "#!/bin/sh\necho called >&2\nexit 99\n"})
    env = _env(tmp_path, bindir, store)
    result = _run("automated_backup.sh", env)
    assert result.returncode == 0, result.stderr
    assert "s3: skipped (BACKUP_S3_URI unset)" in result.stdout
    assert _calls(store) == []
    assert SECRET not in result.stdout and SECRET not in result.stderr


def test_dry_run_prints_minio_upload_without_calling_the_client(tmp_path):
    store = tmp_path / "s3"
    bindir = _tool_dir(tmp_path, {"aws": "#!/bin/sh\necho called >&2\nexit 99\n"})
    env = _env(tmp_path, bindir, store)
    env.update(
        {
            "DRY_RUN": "1",
            "BACKUP_ID": "issue706",
            "BACKUP_S3_URI": URI,
            "BACKUP_S3_ENDPOINT": ENDPOINT,
            "POSTGRES_DUMP_FILE": str(tmp_path / "missing.sql"),
        }
    )
    result = _run("automated_backup.sh", env)
    assert result.returncode == 0, result.stderr
    assert "s3_endpoint: http://127.0.0.1:9000" in result.stdout
    assert "s3_client: aws or mc (not invoked)" in result.stdout
    assert "would upload delta_lake_issue706.tar.gz to s3://scrapy-backups/pipeline/delta_lake_issue706.tar.gz (not uploaded)" in result.stdout
    assert "would upload postgres_issue706.sql to s3://scrapy-backups/pipeline/postgres_issue706.sql (not uploaded)" in result.stdout
    assert "s3_retention:" in result.stdout
    assert "remote objects are not deleted" in result.stdout
    assert _calls(store) == []
    assert "called" not in result.stderr


def test_rejects_credentials_inside_the_uri(tmp_path):
    store = tmp_path / "s3"
    bindir = _tool_dir(tmp_path, {})
    env = _env(tmp_path, bindir, store)
    env["BACKUP_S3_URI"] = "s3://AKIATEST:s3cret-706-ZZ@scrapy-backups/pipeline"
    result = _run("automated_backup.sh", env)
    assert result.returncode == 2
    assert "no credentials" in result.stderr
    assert SECRET not in result.stdout


@pytest.mark.parametrize("client", ["aws", "mc"])
def test_upload_and_restore_against_the_minio_endpoint(tmp_path, work, client):
    store = tmp_path / "s3"
    extra = {"aws": AWS_FAKE} if client == "aws" else {"mc": MC_FAKE}
    bindir = _tool_dir(tmp_path, extra)
    env = _env(tmp_path, bindir, store)
    src, sql = _lake(work)
    backup = work / "backups"
    env.update(
        {
            "DRY_RUN": "0",
            "LAKE_SRC": str(src),
            "BACKUP_DIR": str(backup),
            "BACKUP_ID": "issue706",
            "POSTGRES_DUMP_FILE": str(sql),
            "RETENTION_DAYS": "0",
            "RETENTION_COUNT": "0",
            "BACKUP_S3_URI": URI,
            "BACKUP_S3_ENDPOINT": ENDPOINT,
            "BACKUP_S3_CLIENT": client,
            "AWS_ACCESS_KEY_ID": ACCESS,
            "AWS_SECRET_ACCESS_KEY": SECRET,
        }
    )
    result = _run("automated_backup.sh", env)
    assert result.returncode == 0, result.stderr + result.stdout
    assert f"s3_client: {client}" in result.stdout
    assert "uploaded: s3://scrapy-backups/pipeline/delta_lake_issue706.tar.gz" in result.stdout
    assert "uploaded: s3://scrapy-backups/pipeline/postgres_issue706.sql" in result.stdout
    assert SECRET not in result.stdout and SECRET not in result.stderr
    assert ACCESS not in result.stdout
    ops = [row["op"] for row in _calls(store)]
    assert "upload" in ops
    lake_obj = store / "buckets" / "scrapy-backups" / "pipeline" / "delta_lake_issue706.tar.gz"
    sql_obj = store / "buckets" / "scrapy-backups" / "pipeline" / "postgres_issue706.sql"
    assert lake_obj.is_file() and sql_obj.read_text() == "SELECT 1;\n"
    assert not list((tmp_path / "tmp").glob("scrapy-s3.*"))

    restore_dir = work / "from-s3"
    dest = work / "restored"
    sql_dest = work / "restored.sql"
    env["BACKUP_DIR"] = str(restore_dir)
    env["RESTORE_DEST"] = str(dest)
    env["RESTORE_SQL_DEST"] = str(sql_dest)
    env["DRY_RUN"] = "0"
    (store / "calls.log").unlink()
    restored = _run("restore_backup.sh", env)
    assert restored.returncode == 0, restored.stderr + restored.stdout
    assert "downloaded: s3://scrapy-backups/pipeline/delta_lake_issue706.tar.gz" in restored.stdout
    assert (dest / "sample_table" / "part.txt").read_text() == "marker-706\n"
    assert sql_dest.read_text() == "SELECT 1;\n"
    assert SECRET not in restored.stdout and SECRET not in restored.stderr
    assert any(row["op"] == "download" for row in _calls(store))
    assert not list((tmp_path / "tmp").glob("scrapy-s3.*"))


def test_missing_minio_credentials_fail_before_writing_the_archive(tmp_path, work):
    store = tmp_path / "s3"
    bindir = _tool_dir(tmp_path, {"aws": AWS_FAKE})
    env = _env(tmp_path, bindir, store)
    src, _sql = _lake(work)
    backup = work / "backups"
    env.update(
        {
            "DRY_RUN": "0",
            "LAKE_SRC": str(src),
            "BACKUP_DIR": str(backup),
            "BACKUP_ID": "issue706",
            "RETENTION_DAYS": "0",
            "BACKUP_S3_URI": URI,
            "BACKUP_S3_ENDPOINT": ENDPOINT,
        }
    )
    result = _run("automated_backup.sh", env)
    assert result.returncode == 2
    assert "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are required" in result.stderr
    assert not backup.exists()
    assert _calls(store) == []


def test_missing_client_fails_before_writing_the_archive(tmp_path, work):
    store = tmp_path / "s3"
    bindir = _tool_dir(tmp_path, {})
    env = _env(tmp_path, bindir, store)
    src, _sql = _lake(work)
    backup = work / "backups"
    env.update(
        {
            "DRY_RUN": "0",
            "LAKE_SRC": str(src),
            "BACKUP_DIR": str(backup),
            "BACKUP_ID": "issue706",
            "RETENTION_DAYS": "0",
            "BACKUP_S3_URI": URI,
            "BACKUP_S3_ENDPOINT": ENDPOINT,
            "AWS_ACCESS_KEY_ID": ACCESS,
            "AWS_SECRET_ACCESS_KEY": SECRET,
        }
    )
    result = _run("automated_backup.sh", env)
    assert result.returncode == 1
    assert "aws or mc is required" in result.stderr
    assert not backup.exists()


def test_roundtrip_stays_local_when_s3_uri_is_exported(tmp_path):
    store = tmp_path / "s3"
    bindir = _tool_dir(tmp_path, {"aws": "#!/bin/sh\necho called >&2\nexit 99\n"})
    env = _env(tmp_path, bindir, store)
    env.update(
        {
            "ROUNDTRIP": "1",
            "BACKUP_S3_URI": URI,
            "BACKUP_S3_ENDPOINT": ENDPOINT,
            "AWS_ACCESS_KEY_ID": ACCESS,
            "AWS_SECRET_ACCESS_KEY": SECRET,
        }
    )
    result = _run("restore_backup.sh", env)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "roundtrip: ok" in result.stdout
    assert "called" not in result.stderr
    assert _calls(store) == []


def test_restore_dry_run_documents_the_download(tmp_path):
    store = tmp_path / "s3"
    bindir = _tool_dir(tmp_path, {"aws": "#!/bin/sh\nexit 99\n"})
    env = _env(tmp_path, bindir, store)
    env.update(
        {
            "DRY_RUN": "1",
            "BACKUP_DIR": str(tmp_path / "missing-backups"),
            "BACKUP_ID": "issue706",
            "BACKUP_S3_URI": URI,
            "BACKUP_S3_ENDPOINT": ENDPOINT,
            "RESTORE_DEST": str(tmp_path / "dest"),
        }
    )
    result = _run("restore_backup.sh", env)
    assert result.returncode == 0, result.stderr
    assert "would download s3://scrapy-backups/pipeline/delta_lake_issue706.tar.gz" in result.stdout
    assert "not downloaded" in result.stdout
    assert "s3_retention:" in result.stdout
    assert not (tmp_path / "dest").exists()


def test_backup_profile_is_loopback_minio_and_docs_name_credentials():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    minio = compose["services"]["minio"]
    assert minio["profiles"] == ["backup"]
    assert minio["image"] == "alpine/minio:RELEASE.2025-10-15T17-29-55Z"
    assert "127.0.0.1:9000:9000" in minio["ports"]
    assert "127.0.0.1:9001:9001" in minio["ports"]
    assert minio["user"] == "0:0"
    env = "\n".join(minio["environment"])
    assert "${MINIO_ROOT_USER:-scrapybackup}" in env
    assert "${MINIO_ROOT_PASSWORD:-change_me_minio}" in env
    assert "minio/health/live" in " ".join(minio["healthcheck"]["test"])
    assert "minio-data" in compose["volumes"]
    for service, body in compose["services"].items():
        if service == "minio":
            continue
        assert "backup" not in (body.get("profiles") or [])

    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for var in (
        "BACKUP_S3_URI",
        "BACKUP_S3_ENDPOINT",
        "BACKUP_S3_CLIENT",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
    ):
        assert var in example
    readme = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
    assert "BACKUP_S3_URI" in readme
    assert "expire-days" in readme
    assert "remote objects" in readme
    assert "RESTORE_DEST" in readme and "BACKUP_ID" in readme
    assert "AWS_ACCESS_KEY_ID" in readme and "AWS_SECRET_ACCESS_KEY" in readme
    compose_doc = (ROOT / "COMPOSE.md").read_text(encoding="utf-8")
    assert "| `backup` |" in compose_doc
    assert "127.0.0.1:9000" in compose_doc
    assert '"backup"' in (ROOT / "shutdown.py").read_text(encoding="utf-8")
    assert "minio" in (ROOT / "scripts" / "complete_reset.sh").read_text(encoding="utf-8")
