"""Crawl politely with the deep-dive spider configuration."""

import logging

from src.common.config import Config
from src.common.spider_config import get_spider_settings
from src.stage1.base_spider import BaseSpider

logger = logging.getLogger(__name__)


class DeepDiveSpider(BaseSpider):
    """Throttle-friendly crawler that leans on the depth middleware."""

    name = "deep_dive"

    # Load the depth-focused configuration
    custom_settings = get_spider_settings("deep_dive")

    def __init__(self, *args, **kwargs):
        """Initialize with optional domain allowlist overrides."""
        super().__init__(*args, **kwargs)

        config_instance = Config.get_instance()
        config = config_instance._config
        stage1_config = config.get("stage1", {})
        configured_domains = stage1_config.get("allowed_domains", [])

        if configured_domains:
            self.allowed_domains = configured_domains
            logger.info(f"[K4] DeepDiveSpider enforcing domain allowlist from config: {configured_domains}")
        else:
            logger.info(f"[K4] DeepDiveSpider using dynamic domains: {self.allowed_domains}")
