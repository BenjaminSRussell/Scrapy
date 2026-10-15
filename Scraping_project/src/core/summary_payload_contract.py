"""Shared stage2→stage3 summary payload contract (#668).

The row Stage 2 hands Stage 3 is a ``stage2_page_analysis`` record. Its
required fields and types are the ``pages`` contract in
``docs/contracts/pages.schema.json`` (additional properties allowed). A
missing count, source identifier, or content field must fail here instead of
being defaulted into a row that only looks valid. Stage 2 serializes with
this check and Stage 3 validates with it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, NoReturn


class SummaryPayloadError(ValueError):
    """The stage2→stage3 summary payload cannot be scored as-is."""


# Same order as ``required`` in docs/contracts/pages.schema.json.
REQUIRED_SUMMARY_FIELDS: tuple[str, ...] = (
    "url",
    "url_hash",
    "title",
    "word_count",
    "content_length",
    "html_length",
    "text_to_html_ratio",
    "is_low_quality",
    "is_massive_doc",
    "quality_score",
    "text_content",
    "keywords",
    "has_error",
    "processed_at",
)

# bool is a subclass of int, so counts and scores reject it in _check_field.
SUMMARY_FIELD_TYPES: dict[str, tuple[type, ...]] = {
    "url": (str,),
    "url_hash": (str,),
    "title": (str,),
    "word_count": (int,),
    "content_length": (int,),
    "html_length": (int,),
    "text_to_html_ratio": (int, float),
    "is_low_quality": (bool,),
    "is_massive_doc": (bool,),
    "quality_score": (int, float),
    "text_content": (str,),
    "keywords": (list,),
    "has_error": (bool,),
    "processed_at": (str,),
}

_IDENTIFIERS = frozenset({"url", "url_hash"})
_PLAIN_STRINGS = frozenset({"title", "text_content", "processed_at"})
_COUNTS = frozenset({"word_count", "content_length", "html_length"})
_NUMBERS = frozenset({"text_to_html_ratio", "quality_score"})
_BOOLS = frozenset({"is_low_quality", "is_massive_doc", "has_error"})


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _fail(message: str) -> NoReturn:
    raise SummaryPayloadError(message)


def _expected_label(field: str) -> str:
    if field in _NUMBERS:
        return "a number"
    if field == "keywords":
        return "list"
    if field in _BOOLS:
        return "bool"
    return SUMMARY_FIELD_TYPES[field][0].__name__


def checked_summary_payload(record: Any) -> dict[str, Any]:
    """Copy ``record`` when every required field is present and typed.

    Extra keys are preserved. ``keywords`` is copied so a later append on the
    input list does not change the payload Stage 3 would score.
    """
    if isinstance(record, (str, bytes, bytearray)) or not isinstance(record, Mapping):
        _fail(f"summary payload is malformed: expected a JSON object, got {_type_name(record)}")

    for field in REQUIRED_SUMMARY_FIELDS:
        if field not in record:
            _fail(f"summary payload field {field!r} is missing")
        value = record[field]
        if value is None:
            _fail(f"summary payload field {field!r} is null")
        _check_field(field, value)

    copied = dict(record)
    copied["keywords"] = list(record["keywords"])
    return copied


def _reject_type(field: str, value: Any) -> None:
    _fail(f"summary payload field {field!r} must be {_expected_label(field)}, got {_type_name(value)}")


def _check_field(field: str, value: Any) -> None:
    if field in _IDENTIFIERS:
        if not isinstance(value, str):
            _reject_type(field, value)
        # pages.schema.json minLength 1. Whitespace is not a source id.
        if not value.strip():
            _fail(f"summary payload field {field!r} is blank")
        return

    if field in _PLAIN_STRINGS:
        # Empty title, text, or timestamp string is present. Stage 2 writes ""
        # for low-quality text. Null is rejected by the caller.
        if not isinstance(value, str):
            _reject_type(field, value)
        return

    if field in _COUNTS:
        # bool is an int subclass and is not a count. minimum is 0.
        if isinstance(value, bool) or not isinstance(value, int):
            _reject_type(field, value)
        if value < 0:
            _fail(f"summary payload field {field!r} must be >= 0, got {value}")
        return

    if field == "keywords":
        if not isinstance(value, list):
            _reject_type(field, value)
        # Stage 2 stores [""] when YAKE returns nothing. Empty strings stay;
        # a null or non-string item is not content metadata.
        items: list[Any] = value
        for index, item in enumerate(items):
            if item is None:
                _fail(f"summary payload field 'keywords' item {index} is null")
            if not isinstance(item, str):
                _fail(
                    f"summary payload field 'keywords' item {index} must be str, got {_type_name(item)}"
                )
        return

    if field in _NUMBERS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _reject_type(field, value)
        if isinstance(value, float) and not math.isfinite(value):
            _fail(f"summary payload field {field!r} must be a finite number, got {value!r}")
        return

    if field in _BOOLS:
        if not isinstance(value, bool):
            _reject_type(field, value)
        return

    _fail(f"summary payload field {field!r} is not part of the contract")
