import scrapy

class UConnItem(scrapy.Item):
    url            = scrapy.Field()
    url_hash       = scrapy.Field()
    status         = scrapy.Field()
    content_type   = scrapy.Field()
    size_bytes     = scrapy.Field()
    word_count     = scrapy.Field()
    pdf_present    = scrapy.Field()
    audio_present  = scrapy.Field()
    entities       = scrapy.Field()  # List[str]
    keywords       = scrapy.Field()  # List[str] – unsupervised
    tags           = scrapy.Field()  # heuristic category labels