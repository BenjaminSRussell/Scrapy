import scrapy

class OffsiteCandidateItem(scrapy.Item):

    source_page = scrapy.Field()
    external_url = scrapy.Field()
    anchor_text = scrapy.Field()
    context = scrapy.Field()
    discovered_at = scrapy.Field()
