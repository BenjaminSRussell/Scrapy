"""Scout Spider - Aggressive URL discovery with maximum concurrency.

This spider extends BaseSpider with aggressive crawling settings optimized for
rapid URL discovery across the target domain.
"""

from src.common.spider_config import get_spider_settings
from src.stage1.base_spider import BaseSpider


class ScoutSpider(BaseSpider):
    """Aggressive scout spider optimized for maximum URL discovery rate."""

    name = "scout"

    # Load custom settings from config.yml
    custom_settings = get_spider_settings("scout")

    def __init__(self, *args, **kwargs):
        """Initialize scout spider with aggressive settings."""
        super().__init__(*args, **kwargs)
