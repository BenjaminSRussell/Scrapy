import sys
from scrapy.crawler import CrawlerProcess
from scrapy_uconn.spiders.advanced_spider import AdvancedUConnSpider
from scrapy_uconn import settings as project_settings

if __name__ == "__main__":
    process = CrawlerProcess(settings=project_settings.__dict__)
    process.crawl(AdvancedUConnSpider)
    process.start()