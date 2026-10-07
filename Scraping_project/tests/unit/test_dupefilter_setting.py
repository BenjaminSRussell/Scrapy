"""#197: production settings must use a real request dupefilter."""

import scrapy
from scrapy.core.scheduler import Scheduler
from scrapy.dupefilters import BaseDupeFilter, RFPDupeFilter
from scrapy.utils.misc import build_from_crawler, load_object
from scrapy.utils.test import get_crawler

from src import settings as project_settings


def _crawler():
    # Only the setting under test: the project's reactor choice is irrelevant here.
    return get_crawler(scrapy.Spider, settings_dict={"DUPEFILTER_CLASS": project_settings.DUPEFILTER_CLASS})


def test_project_dupefilter_is_not_the_noop_base():
    cls = load_object(project_settings.DUPEFILTER_CLASS)
    assert issubclass(cls, RFPDupeFilter)
    assert cls is not BaseDupeFilter


def test_duplicate_requests_are_dropped():
    crawler = _crawler()
    df = build_from_crawler(load_object(crawler.settings["DUPEFILTER_CLASS"]), crawler)
    df.open()
    try:
        assert df.request_seen(scrapy.Request("https://catalog.uconn.edu/page?a=1&b=2")) is False
        # Same resource with reordered query: canonical fingerprint matches.
        assert df.request_seen(scrapy.Request("https://catalog.uconn.edu/page?b=2&a=1")) is True
        assert df.request_seen(scrapy.Request("https://catalog.uconn.edu/other")) is False
    finally:
        df.close("finished")


def test_scheduler_drops_duplicates_but_honours_dont_filter():
    crawler = _crawler()
    sched = Scheduler.from_crawler(crawler)
    crawler.spider = scrapy.Spider.from_crawler(crawler, name="t")
    sched.open(crawler.spider)
    try:
        url = "https://uconn.edu/"
        assert sched.enqueue_request(scrapy.Request(url)) is True
        assert sched.enqueue_request(scrapy.Request(url)) is False  # duplicate dropped
        assert sched.enqueue_request(scrapy.Request(url, dont_filter=True)) is True  # seeds/retries
    finally:
        sched.close("finished")
