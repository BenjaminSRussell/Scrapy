"""
Scrapy middleware for adaptive crawl depth.
"""

import logging
from scrapy.exceptions import NotConfigured
from .adaptive_depth import AdaptiveDepthManager

logger = logging.getLogger(__name__)

class AdaptiveDepthMiddleware:
    def __init__(self, settings):
        if not settings.getbool("ADAPTIVE_DEPTH_ENABLED"):
            raise NotConfigured

        config_path = settings.get("ADAPTIVE_DEPTH_CONFIG_FILE")
        if not config_path:
            raise NotConfigured("ADAPTIVE_DEPTH_CONFIG_FILE must be set.")

        self.manager = AdaptiveDepthManager(config_file=config_path)
        logger.info("Adaptive Depth Middleware enabled.")

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def process_request(self, request, spider):
        if "max_depth" not in request.meta:
            depth = self.manager.get_depth_for_url(request.url)
            request.meta["max_depth"] = depth
            logger.debug(f"Set max_depth={depth} for {request.url}")
        return None
