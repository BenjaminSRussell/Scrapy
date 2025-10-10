"""Scrapy Items for the scraping project.

This module defines custom Scrapy Item classes for structured data collection.
"""

import scrapy


class OffsiteCandidateItem(scrapy.Item):
    """Item for external URLs discovered during crawling.

    This item represents a URL that points outside the primary domain
    (e.g., external links from uconn.edu). These URLs are candidates
    for future classification and potential crawling.

    Fields:
        source_page: The page where this external URL was found
        external_url: The external URL discovered
        anchor_text: The text of the link (if available)
        context: Surrounding paragraph or sentence text
        discovered_at: ISO timestamp when the link was discovered
    """

    source_page = scrapy.Field()
    external_url = scrapy.Field()
    anchor_text = scrapy.Field()
    context = scrapy.Field()
    discovered_at = scrapy.Field()
