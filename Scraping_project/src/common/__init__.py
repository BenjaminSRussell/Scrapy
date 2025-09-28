"""Common shared utilities and project-level compatibility patches."""

from __future__ import annotations

from typing import Dict


def _patch_scrapy_response_meta() -> None:
    """Allow assigning dictionaries to ``response.meta`` in tests.

    Scrapy normally exposes ``Response.meta`` as a read-only proxy to the
    underlying request metadata. Our test suite creates synthetic responses and
    expects to assign a fresh dictionary directly. We provide a lightweight
    setter that materialises a request when necessary and copies the provided
    mapping so core Scrapy behaviour remains unchanged.
    """

    try:
        from scrapy.http import Request, Response  # type: ignore
    except Exception:
        return

    if getattr(Response, "_meta_assignment_patched", False):
        return

    original_property = getattr(Response, "meta", None)
    if not isinstance(original_property, property):
        return

    def _meta_get(instance: Response) -> Dict:
        return original_property.fget(instance)  # type: ignore[attr-defined]

    def _meta_set(instance: Response, value: Dict) -> None:
        if value is None:
            value_dict: Dict = {}
        elif isinstance(value, dict):
            value_dict = value
        else:
            raise TypeError("Response.meta assignments must use a dict")

        if getattr(instance, "request", None) is None:
            instance.request = Request(url=getattr(instance, "url", ""), dont_filter=True)

        instance.request.meta.clear()
        instance.request.meta.update(value_dict)

    Response.meta = property(  # type: ignore[assignment]
        _meta_get,
        _meta_set,
        doc=original_property.__doc__,
    )
    Response._meta_assignment_patched = True  # type: ignore[attr-defined]


_patch_scrapy_response_meta()


__all__: tuple[str, ...] = ()
