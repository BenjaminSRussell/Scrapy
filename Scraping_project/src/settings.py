# Scrapy settings for uconn_scraper project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = 'uconn_scraper'

SPIDER_MODULES = ['src.stage1', 'src.stage3']
NEWSPIDER_MODULE = 'src.stage3'

# Obey robots.txt rules
ROBOTSTXT_OBEY = False

# Configure pipelines
ITEM_PIPELINES = {
    'src.stage3.enrichment_pipeline.Stage3Pipeline': 300,
}

# Configure request fingerprinting
REQUEST_FINGERPRINTER_IMPLEMENTATION = '2.7'

# Enable and configure the AutoThrottle extension (disabled by default)
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.1
AUTOTHROTTLE_MAX_DELAY = 1.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 8.0
AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching
HTTPCACHE_ENABLED = True
HTTPCACHE_EXPIRATION_SECS = 3600
HTTPCACHE_DIR = 'data/cache/scrapy'

# Set settings whose default value is deprecated to a future-proof value
TWISTED_REACTOR = 'twisted.internet.asyncioreactor.AsyncioSelectorReactor'
FEED_EXPORT_ENCODING = 'utf-8'