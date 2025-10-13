"""Deep Dive Spider - Conservative, respectful crawling for thorough discovery.

This spider extends BaseSpider with conservative crawling settings optimized for
respectful, thorough URL discovery while avoiding server overload.

K4 ACTIVATION: Enforces domain allowlist and seed list from config with DepthMiddleware.
"""

import logging

from src.common.config import load_config
from src.common.spider_config import get_spider_settings
from src.stage1.base_spider import BaseSpider

logger = logging.getLogger(__name__)


class DeepDiveSpider(BaseSpider):
    """Conservative deep dive spider optimized for respectful crawling.

    K4 FEATURES:
    - DepthMiddleware enabled via spider_config (MAX_DEPTH enforcement)
    - Domain allowlist enforced from config.yml
    - Seed list loaded from Delta Lake (no new storage)
    - Same item schema as other spiders (Stage 2 compatibility maintained)
    """

    name = "deep_dive"

    # Load custom settings from config.yml (includes K4 DepthMiddleware)
    custom_settings = get_spider_settings("deep_dive")

    def __init__(self, *args, **kwargs):
        """Initialize deep dive spider with conservative settings.

        K4: Enforces domain allowlist from config.yml (no new storage).
        """
        super().__init__(*args, **kwargs)

        # K4: Load and enforce domain allowlist from config
        config = load_config()
        stage1_config = config.get('stage1', {})
        configured_domains = stage1_config.get('allowed_domains', [])

        if configured_domains:
            self.allowed_domains = configured_domains
            logger.info(f"[K4] DeepDiveSpider enforcing domain allowlist from config: {configured_domains}")
        else:
            # Fall back to dynamic extraction from start_urls (BaseSpider behavior)
            logger.info(f"[K4] DeepDiveSpider using dynamic domains: {self.allowed_domains}")
