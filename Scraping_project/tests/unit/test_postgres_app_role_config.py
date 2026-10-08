"""#538: least-privilege Postgres app role, consistent across init SQL, Compose, Helm and the app."""

import base64
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INIT = ROOT / "docker" / "init-db.sql"
HELM_INIT = ROOT / "k8s" / "helm" / "scraping-pipeline" / "files" / "init-db.sql"
CHART = ROOT / "k8s" / "helm" / "scraping-pipeline"


def test_init_sql_copies_identical():
    assert INIT.read_text() == HELM_INIT.read_text()


def test_init_sql_owns_all_app_tables_and_grants_least_privilege():
    from src.utils.postgres import SCHEMA_TABLES
    sql = INIT.read_text()
    for table in SCHEMA_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in sql, table
    grants = re.findall(r"GRANT ([A-Z ,]+) ON (?!DATABASE|SCHEMA|ALL SEQUENCES)", sql)
    assert grants == ["SELECT, INSERT"], grants
    assert "GRANT ALL" not in sql
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in sql
    assert "NOSUPERUSER" in sql and "%L" in sql  # password quoted by format(), not interpolated


def _compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def _env(svc):
    return dict(item.split("=", 1) for item in svc.get("environment", []))


def test_compose_mounts_init_sql_and_workers_use_app_role():
    services = _compose()
    pg = services["postgres"]
    assert any(v.startswith("./docker/init-db.sql:/docker-entrypoint-initdb.d/") for v in pg["volumes"])
    pg_env = _env(pg)
    app_user = pg_env["APP_DB_USER"]
    assert app_user and app_user != pg_env["POSTGRES_USER"]
    users = {name: _env(s)["DB_USER"] for name, s in services.items() if "DB_USER" in _env(s)}
    assert users, "no service connects to Postgres?"
    for name, user in users.items():
        assert user == app_user, f"{name} connects as {user}, expected the app role {app_user}"
        assert _env(services[name])["DB_PASSWORD"] == pg_env["APP_DB_PASSWORD"], name


# --- app: no DDL as the restricted role when the schema exists ---------------------------

def _manager(present):
    from src.utils.postgres import PostgresManager
    m = PostgresManager.__new__(PostgresManager)
    m.user = "scrapy_app"
    cur = MagicMock()
    cur.fetchone.return_value = present
    conn = MagicMock()
    conn.cursor.return_value = cur
    cm = MagicMock()
    cm.__enter__.return_value = conn
    m.get_connection = MagicMock(return_value=cm)
    return m, cur


def test_schema_present_skips_ddl():
    m, cur = _manager(("a", "b", "c", "d"))
    with patch.object(type(m), "_create_schema") as create:
        m._initialize_schema()
    create.assert_not_called()
    assert "to_regclass" in cur.execute.call_args[0][0]


def test_schema_missing_creates_and_explains_permission_errors(caplog):
    m, _ = _manager(("a", None, "c", "d"))
    with patch.object(type(m), "_create_schema") as create:
        m._initialize_schema()
    create.assert_called_once()

    err = Exception("permission denied for schema public")
    err.pgcode = "42501"
    with patch.object(type(m), "_create_schema", side_effect=err), pytest.raises(Exception):
        m._initialize_schema()
    assert any("init-db.sql" in r.getMessage() for r in caplog.records)


# --- Helm ------------------------------------------------------------------------------

HELM = shutil.which("helm")
BASE = ["--set", "secrets.useExternalSecrets=false", "--set", "secrets.postgres.password=S3cure-super",
        "--set", "secrets.grafana.adminPassword=G-pass-1", "--set", "secrets.redis.password=R-pass-1"]


def _render(*extra):
    r = subprocess.run([HELM, "template", "t", str(CHART), *BASE, *extra], capture_output=True, text=True,
                       timeout=120)
    return r.returncode, r.stdout + r.stderr


def _docs(out):
    return [d for d in yaml.safe_load_all(out) if d]


def _find(docs, kind, name_part):
    return next(d for d in docs if d["kind"] == kind and name_part in d["metadata"]["name"])


@pytest.mark.skipif(HELM is None, reason="helm not installed")
def test_helm_default_unchanged_and_dsn_port_is_an_integer():
    rc, out = _render()
    assert rc == 0, out
    docs = _docs(out)
    cm = _find(docs, "ConfigMap", "app-env")["data"]
    assert cm["DB_USER"] == "postgres" and "APP_DB_USER" not in cm
    sec = _find(docs, "Secret", "postgres-credentials")["data"]
    assert "APP_DB_PASSWORD" not in sec
    for key in ("DATABASE_URL", "DATA_SOURCE_NAME"):
        assert ":5432/" in base64.b64decode(sec[key]).decode(), key  # was "%!d(float64=5432)"


@pytest.mark.skipif(HELM is None, reason="helm not installed")
def test_helm_app_role_opt_in():
    rc, out = _render("--set", "postgresql.auth.appUsername=scrapy_app", "--set", "secrets.postgres.appPassword=App-pass-9")
    assert rc == 0, out
    docs = _docs(out)
    cm = _find(docs, "ConfigMap", "app-env")["data"]
    assert cm["DB_USER"] == cm["APP_DB_USER"] == "scrapy_app"
    assert cm["POSTGRES_USER"] == "postgres"  # superuser kept for init/migrations
    sec = {k: base64.b64decode(v).decode() for k, v in _find(docs, "Secret", "postgres-credentials")["data"].items()}
    assert sec["DB_PASSWORD"] == sec["APP_DB_PASSWORD"] == "App-pass-9"
    assert sec["POSTGRES_PASSWORD"] == "S3cure-super"
    assert sec["DATABASE_URL"].startswith("postgresql://scrapy_app:App-pass-9@")
    assert sec["DATA_SOURCE_NAME"].startswith("postgresql://postgres:")  # exporter unchanged
    assert "user: scrapy_app" in out  # Grafana datasource


@pytest.mark.skipif(HELM is None, reason="helm not installed")
@pytest.mark.parametrize("pw", [None, "S3cure-super", "scrapy_app_dev"])
def test_helm_app_role_requires_distinct_secure_password(pw):
    extra = ["--set", "postgresql.auth.appUsername=scrapy_app"]
    if pw:
        extra += ["--set", f"secrets.postgres.appPassword={pw}"]
    rc, out = _render(*extra)
    assert rc != 0 and "appPassword must be a secure password" in out
