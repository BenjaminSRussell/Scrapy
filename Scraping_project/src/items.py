"""Scrapy items emitted by Stage 1 spiders and the optional people parser.

Most Stage 1 output is plain dicts validated by the ingest contract
(``src.core.ingest_contract``). ``OffsiteCandidateItem`` is the typed offsite
row; ``OffsiteCandidatePipeline`` drops instances that miss a required field
(#247). ``PersonItem`` is the faculty/people directory row (#642). Stage 2
emits one only when ``PEOPLE_DIRECTORY_PARSE_ENABLED`` is on and confidence
is at least ``PersonItem.MIN_CONFIDENCE``.
"""

from __future__ import annotations

import re
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


class PersonItem(scrapy.Item):
    """One faculty or staff person: name, email, and title (#642).

    ``confidence`` is required to validate and must be in
    ``[MIN_CONFIDENCE, 1]``. ``source_url`` is the page the card was read from.
    """

    name = scrapy.Field()
    email = scrapy.Field()
    title = scrapy.Field()
    source_url = scrapy.Field()
    confidence = scrapy.Field()

    REQUIRED_FIELDS: tuple[str, ...] = ("name", "email", "title")
    MIN_CONFIDENCE: float = 0.85
    EMAIL_RE: re.Pattern[str] = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$")

    def missing_required(self) -> list[str]:
        """Required fields that are absent, not a string, or blank, in declaration order."""
        missing = []
        for field in self.REQUIRED_FIELDS:
            value: Any = self.get(field)
            if not isinstance(value, str) or not value.strip():
                missing.append(field)
        return missing

    def normalized_email(self) -> str | None:
        """Lowercased address, or None when the field is not one address."""
        value = self.get("email")
        if not isinstance(value, str):
            return None
        text = value.strip().lower()
        if self.EMAIL_RE.fullmatch(text) is None:
            return None
        return text

    def confidence_value(self) -> float | None:
        """Confidence in range, or None when it is missing, NaN, or too low."""
        value = self.get("confidence")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if number != number or number > 1.0 + 1e-9 or number + 1e-9 < self.MIN_CONFIDENCE:
            return None
        return number

    def validate(self) -> None:
        """Raise ``ValueError`` when required fields, email, or confidence fail."""
        missing = self.missing_required()
        if missing:
            raise ValueError(f"PersonItem missing required field(s): {', '.join(missing)}")
        if self.normalized_email() is None:
            raise ValueError("PersonItem email is not a single address")
        if self.confidence_value() is None:
            raise ValueError(
                f"PersonItem confidence {self.get('confidence')!r} is below {self.MIN_CONFIDENCE}"
            )

    def to_record(self) -> dict[str, Any]:
        """Plain dict of the schema fields. Validates first."""
        self.validate()
        email = self.normalized_email()
        confidence = self.confidence_value()
        if email is None or confidence is None:
            raise ValueError("PersonItem failed validation")
        source = self.get("source_url")
        return {
            "name": re.sub(r"\s+", " ", str(self["name"]).strip()),
            "email": email,
            "title": re.sub(r"\s+", " ", str(self["title"]).strip()),
            "source_url": source.strip() if isinstance(source, str) else "",
            "confidence": confidence,
        }
