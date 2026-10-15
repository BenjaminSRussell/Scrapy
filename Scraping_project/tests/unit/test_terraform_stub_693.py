"""Issue 693: Terraform stub is fmt/validate only, with no credentials."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WF = ROOT / ".github" / "workflows" / "ci-terraform.yml"
TF_DIR = ROOT / "infra" / "terraform"


def _tf_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(TF_DIR.glob("*.tf")))


def test_stub_declares_vpc_bucket_and_redis_without_secrets():
    text = _tf_text()
    assert "aws_vpc" in text
    assert "aws_s3_bucket" in text
    assert 'object_store_backend' in text
    assert "minio" in text.lower()
    assert "aws_elasticache_replication_group" in text
    assert "volatile-lru" in text
    assert "redis://" not in text
    assert "access_key" not in text
    assert "secret_key" not in text
    assert "allow_apply" in text
    readme = (ROOT / "infra" / "README.md").read_text(encoding="utf-8")
    assert "Do not set `REDIS_URL`" in readme
    assert "REDIS_HOST" in readme
    assert "REDIS_PASSWORD" in readme
    assert "terraform validate" in readme


def test_ci_formats_and_validates_without_applying():
    body = WF.read_text(encoding="utf-8")
    assert 'terraform fmt -check -diff -recursive' in body
    assert "terraform init -backend=false -input=false" in body
    assert "terraform validate" in body
    assert "terraform apply" not in body
    assert "terraform plan" not in body
    assert "infra/**" in body
    assert "timeout-minutes:" in body
