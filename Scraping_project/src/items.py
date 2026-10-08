"""Scrapy items emitted by Stage 1 spiders.

Most Stage 1 output is plain dicts validated by the ingest contract
(``src.core.ingest_contract``). ``OffsiteCandidateItem`` is the one typed item;
``OffsiteCandidatePipeline`` drops instances that miss a required field (#247).
"""

from __future__ import annotations

from typing import Any

import scrapy


class OffsiteCandidateItem(scrapy.Item):
    """An external link found on a crawled page, kept for allowlist review."""

    source_page = scrapy.Field()
    external_url = scrapy.Field()
    anchor_text = scrapy.Field()
    context = scrapy.Field()
    discovered_at = scrapy.Field()

    #: Fields without which the row is useless to the offsite review table.
    REQUIRED_FIELDS: tuple[str, ...] = ("source_page", "external_url", "discovered_at")

    def missing_required(self) -> list[str]:
        """Required fields that are absent, None or blank, in declaration order."""
        missing = []
        for name in self.REQUIRED_FIELDS:
            value: Any = self.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(name)
        return missing

    def validate(self) -> None:
        """Raise ``ValueError`` naming every missing required field."""
        missing = self.missing_required()
        if missing:
            raise ValueError(f"OffsiteCandidateItem missing required field(s): {', '.join(missing)}")
