"""Prove Scrapy settings derive from config.yml via get_config() (issue #151)."""

import yaml
import pytest

from src.core.config import Config, reset_config
from src.settings import PROJECT_ROOT, derive_scrapy_config


@pytest.mark.unit
def test_scrapy_settings_reflect_temp_config_yml(tmp_path):
    """Changing a key in a temp config.yml changes a Scrapy setting value."""
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        yaml.dump(
            {
                "scrapy": {
                    "concurrent_requests": 4242,
                },
                "kafka": {
                    "bootstrap_servers": "custom-kafka:9092",
                    "topics": {"scraped_items": "custom-topic"},
                },
                "logging": {"level": "DEBUG"},
            }
        )
    )

    reset_config()
    cfg = Config(config_path)
    scrapy = derive_scrapy_config(cfg)

    assert scrapy["concurrent_requests"] == 4242
    assert scrapy["kafka_bootstrap_servers"] == "custom-kafka:9092"
    assert scrapy["kafka_topic"] == "custom-topic"
    assert scrapy["log_level"] == "DEBUG"


@pytest.mark.unit
def test_scrapy_settings_bridge_without_scrapy_section(tmp_path):
    """Without a scrapy: section, settings still bridge from config.yml SSOT keys."""
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        yaml.dump(
            {
                "stage1": {
                    "spiders": {
                        "scout": {
                            "concurrent_requests": 1111,
                            "download_delay": 0.42,
                        }
                    }
                },
                "kafka": {
                    "bootstrap_servers": "bridged:9092",
                    "topics": {"scraped_items": "bridged-items"},
                },
            }
        )
    )

    reset_config()
    cfg = Config(config_path)
    scrapy = derive_scrapy_config(cfg)

    assert scrapy["concurrent_requests"] == 1111
    assert scrapy["download_delay"] == 0.42
    assert scrapy["kafka_bootstrap_servers"] == "bridged:9092"
    assert scrapy["kafka_topic"] == "bridged-items"


@pytest.mark.unit
def test_default_config_yml_drives_scrapy_settings():
    """With project config.yml, derived settings reflect SSOT (not missing ENV yml)."""
    reset_config()
    cfg = Config(PROJECT_ROOT / "config.yml")
    scrapy = derive_scrapy_config(cfg)

    # Values present in Scraping_project/config.yml
    assert scrapy["concurrent_requests"] == 1024  # stage1.spiders.scout
    assert scrapy["kafka_bootstrap_servers"] == "localhost:9092"
    assert scrapy["kafka_topic"] == "scraped-items"
    assert scrapy["log_level"] == "INFO"
