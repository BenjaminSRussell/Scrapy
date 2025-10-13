"""Deep Dive Spider - Conservative, respectful crawling for thorough discovery.

This spider extends BaseSpider with conservative crawling settings optimized for
respectful, thorough URL discovery while avoiding server overload.
"""

from src.common.spider_config import get_spider_settings
from src.stage1.base_spider import BaseSpider


class DeepDiveSpider(BaseSpider):
    """Conservative deep dive spider optimized for respectful crawling."""

    name = "deep_dive"

    # Load custom settings from config.yml
    custom_settings = get_spider_settings("deep_dive")

    def __init__(self, *args, **kwargs):
        """Initialize deep dive spider with conservative settings."""
        super().__init__(*args, **kwargs)
