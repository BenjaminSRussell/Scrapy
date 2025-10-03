# TODO: This patching mechanism is a bit of a hack. We should investigate if we can use a more standard way to handle response.meta in tests, perhaps by creating a proper Scrapy Request object.
"""Common shared utilities and project-level compatibility patches."""

from __future__ import annotations


def _patch_scrapy_response_meta() -> None:
    """Patch Scrapy Response.meta to allow assignment in tests."""

    try:
        from scrapy.http import Request, Response  # type: ignore
    except Exception:
        return

    if getattr(Response, "_meta_assignment_patched", False):
        return

    original_property = getattr(Response, "meta", None)
    if not isinstance(original_property, property):
        return

    def _meta_get(instance: Response) -> dict:
        return original_property.fget(instance)  # type: ignore[attr-defined]

    def _meta_set(instance: Response, value: dict) -> None:
        if value is None:
            value_dict: dict = {}
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
