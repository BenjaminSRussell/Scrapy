"""config.yml schema validation on CLI and orchestrator startup (#237).

No live scrape, Redis, Kafka, or Postgres. The shipped config.yml must pass;
a bad redis.port must fail before the command or the lake is touched.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from src.utils.validation import (
    ConfigValidationError,
    default_config_path,
    validate_config_document,
    validate_startup_config,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]


def _shipped() -> dict:
    path = default_config_path()
    assert path == ROOT / "config.yml"
    assert path.is_file()
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_shipped_config_yml_passes():
    validate_config_document(_shipped())
    validate_startup_config()


def test_redis_port_string_names_the_field():
    data = copy.deepcopy(_shipped())
    data["redis"]["port"] = "6379"
    with pytest.raises(ConfigValidationError, match=r"redis\.port") as exc:
        validate_config_document(data)
    message = str(exc.value)
    assert "got str" in message
    assert "Traceback" not in message


def test_redis_port_bool_is_rejected():
    data = copy.deepcopy(_shipped())
    data["redis"]["port"] = True
    with pytest.raises(ConfigValidationError, match=r"redis\.port") as exc:
        validate_config_document(data)
    assert "got bool" in str(exc.value)


def test_redis_unknown_key_is_a_field_error():
    data = copy.deepcopy(_shipped())
    data["redis"]["prot"] = data["redis"].pop("port")
    with pytest.raises(ConfigValidationError) as exc:
        validate_config_document(data)
    message = str(exc.value)
    assert "redis.prot" in message
    assert "redis.port" in message


def test_kafka_unknown_key_is_a_field_error():
    data = copy.deepcopy(_shipped())
    data["kafka"]["bootsrap_servers"] = data["kafka"].pop("bootstrap_servers")
    with pytest.raises(ConfigValidationError) as exc:
        validate_config_document(data)
    message = str(exc.value)
    assert "kafka.bootsrap_servers" in message
    assert "kafka.bootstrap_servers" in message


def test_kafka_producer_dotted_typo_is_a_field_error():
    data = copy.deepcopy(_shipped())
    producer = data["kafka"]["producer"]
    producer["enable.idempotance"] = producer.pop("enable.idempotence")
    with pytest.raises(ConfigValidationError) as exc:
        validate_config_document(data)
    message = str(exc.value)
    assert "enable.idempotance" in message


def test_top_level_must_be_a_mapping():
    with pytest.raises(ConfigValidationError, match="mapping"):
        validate_config_document(["redis"])
    with pytest.raises(ConfigValidationError, match="mapping"):
        validate_config_document(None)


def test_unparseable_yaml_fails(tmp_path):
    bad = tmp_path / "config.yml"
    bad.write_text("redis: [\n", encoding="utf-8")
    with pytest.raises(ConfigValidationError, match="YAML parse error"):
        validate_startup_config(bad)


def test_missing_config_file_keeps_default_startup(tmp_path, monkeypatch):
    # Config() already falls back to defaults when the file is absent. Startup
    # validation must not invent a new hard failure for that case.
    missing = tmp_path / "absent.yml"
    monkeypatch.setattr("src.utils.validation.default_config_path", lambda: missing)
    validate_startup_config()


def test_cli_help_does_not_validate_config(tmp_path, monkeypatch):
    import cli

    bad = tmp_path / "config.yml"
    bad.write_text("redis: {port: nope}\n", encoding="utf-8")
    monkeypatch.setattr("src.utils.validation.default_config_path", lambda: bad)
    with patch.object(sys, "argv", ["cli.py", "--help"]), pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def test_cli_startup_accepts_shipped_config(monkeypatch):
    import cli

    assert default_config_path().is_file()
    monkeypatch.setattr(cli, "cmd_health", lambda args: None)
    with patch.object(sys, "argv", ["cli.py", "health"]), pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0


def _with_redis_port(value_yaml: str, dest: Path) -> Path:
    """Copy the shipped file with only the redis ``port:`` line changed."""
    text = default_config_path().read_text(encoding="utf-8")
    needle = "port: 6379"
    assert needle in text
    dest.write_text(text.replace(needle, f"port: {value_yaml}", 1), encoding="utf-8")
    return dest


def test_cli_startup_rejects_bad_redis_port(tmp_path, monkeypatch, capsys):
    import cli

    bad = _with_redis_port('"not-a-port"', tmp_path / "config.yml")
    monkeypatch.setattr("src.utils.validation.default_config_path", lambda: bad)

    def should_not_run(args):
        raise AssertionError("command ran despite invalid config")

    monkeypatch.setattr(cli, "cmd_health", should_not_run)
    with patch.object(sys, "argv", ["cli.py", "health"]), pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "redis.port" in err
    assert "got str" in err
    assert "Traceback" not in err


def test_orchestrator_startup_rejects_bad_redis_port(tmp_path, monkeypatch):
    bad = _with_redis_port('"6379"', tmp_path / "config.yml")
    monkeypatch.setattr("src.utils.validation.default_config_path", lambda: bad)
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "lake"))

    from src.orchestrator import pipeline_orchestrator as po

    def _no_delta():
        raise AssertionError("get_delta should not run when config.yml is invalid")

    monkeypatch.setattr(po, "get_delta", _no_delta)
    with pytest.raises(ConfigValidationError, match=r"redis\.port"):
        po.PipelineOrchestrator()


def test_profile_flag_stays_off_by_default():
    import cli

    source = Path(cli.__file__).read_text(encoding="utf-8")
    assert 'default=None, metavar="DIR"' in source
    assert "args.profile" in source
