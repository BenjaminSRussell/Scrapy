"""Developer setup contracts: pre-commit is locked for `make install-dev` (#358) and the
config layer is config.yml only, with a loud fallback when it is missing (#467)."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from src.core.config import Config

ROOT = Path(__file__).resolve().parents[2]


def _pins(path: Path) -> dict[str, str]:
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==(\S+)", line)
        if m:
            pins[m.group(1).lower()] = m.group(2)
    return pins


def test_pre_commit_declared_and_locked_with_its_dependencies():
    assert re.search(r"(?m)^pre-commit\b", (ROOT / "dev-requirements.in").read_text(encoding="utf-8"))
    pins = _pins(ROOT / "dev-requirements.txt")
    for pkg in ("pre-commit", "cfgv", "identify", "nodeenv", "virtualenv", "distlib",
                "filelock", "platformdirs", "pyyaml"):
        assert pkg in pins, f"{pkg} missing from dev-requirements.txt"


def test_make_install_dev_only_uses_declared_tools():
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = make[make.index("install-dev:"):]
    recipe = recipe[: recipe.index("\n\n")]
    assert "pip install -r dev-requirements.txt" in recipe
    # Hooks execute from the git root; the config lives in Scraping_project/.
    assert 'pre-commit install --config "$$(git rev-parse --show-prefix).pre-commit-config.yaml"' in recipe
    assert (ROOT / ".pre-commit-config.yaml").is_file()


def test_settings_do_not_load_a_config_env_yaml_layer():
    settings = (ROOT / "src" / "settings.py").read_text(encoding="utf-8")
    assert "CONFIG_PATH" not in settings
    assert not re.search(r"config/\{?ENV\}?\.yml\"|f\"config/", settings)
    assert "get_config" in settings


def test_default_config_is_the_shipped_config_yml():
    cfg = Config()
    assert cfg.config_path == ROOT / "config.yml"
    assert cfg.config_path.is_file()


def test_missing_config_file_warns_and_uses_defaults(tmp_path, caplog):
    missing = tmp_path / "nope.yml"
    with caplog.at_level(logging.WARNING, logger="src.core.config"):
        cfg = Config(config_path=missing)
        cfg.get("redis.host")
    assert any("Config file not found" in r.getMessage() and str(missing) in r.getMessage()
               for r in caplog.records)


def test_example_config_points_at_config_yml():
    text = (ROOT / "config" / "entity_summarization.example.yml").read_text(encoding="utf-8")
    assert "development.yml or production.yml" not in text
    assert "config.yml" in text
