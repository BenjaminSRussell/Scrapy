"""
Scrapy middlewares for Stage 1 spiders.

These modules provide middleware functionality for the URL discovery spiders.

Modules:
- retry_middleware.py - Retry logic for failed requests
- spider_config.py - Spider configuration management
- proxy_rotation_middleware.py - Optional egress proxy rotation (#198)
- host_politeness_middleware.py - Inherited robots Crawl-Delay from discovery (#939)
"""

__all__ = []
