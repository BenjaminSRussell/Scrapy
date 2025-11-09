"""Common shared utilities and project-level compatibility patches."""

from __future__ import annotations

def _patch_scrapy_response_meta() -> None:
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
        fget = original_property.fget
        if fget is None:
            return {}
        return fget(instance)  # type: ignore[attr-defined]

    def _meta_set(instance: Response, value: dict) -> None:
        if value is None:
            value_dict: dict = {}
        elif isinstance(value, dict):
            value_dict = value
        else:
            raise TypeError("Response.meta assignments must use a dict")

        request = getattr(instance, "request", None)
        if request is None:
            request = Request(url=getattr(instance, "url", ""), dont_filter=True)
            instance.request = request

        request.meta.clear()
        request.meta.update(value_dict)

    Response.meta = property(  # type: ignore[assignment]
        _meta_get,
        _meta_set,
        doc=original_property.__doc__,
    )
    Response._meta_assignment_patched = True  # type: ignore[attr-defined]

_patch_scrapy_response_meta()

# ============================================================================
# BACKWARD COMPATIBILITY - Reorganization in progress
# ============================================================================
# This package is being reorganized. Please update imports to use:
# - src.utils.delta for Delta Lake operations
# - src.utils.redis for Redis operations
# - src.utils.validation for validation functions
# - src.core.config for configuration
# - src.core.constants for constants
# - src.core.exceptions for exceptions
# ============================================================================

import warnings

# Re-export from new locations for backward compatibility
try:
    from src.utils.delta import get_delta, DeltaHelper
    from src.utils.redis import get_redis, RedisHelper
    from src.utils.validation import is_valid_url, is_uconn_domain
    from src.core.config import get_config, Config
    from src.core.constants import *
    from src.core.exceptions import *

    # Legacy names for backward compatibility
    def get_delta_manager():
        """DEPRECATED: Use get_delta() instead."""
        warnings.warn(
            "get_delta_manager() is deprecated. Use get_delta() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        return get_delta()

    class RedisManager:
        """DEPRECATED: Use get_redis() instead."""
        def __init__(self, *args, **kwargs):
            warnings.warn(
                "RedisManager is deprecated. Use get_redis() instead.",
                DeprecationWarning,
                stacklevel=2
            )
            self._helper = get_redis()

        def __getattr__(self, name):
            return getattr(self._helper, name)

    __all__: tuple[str, ...] = (
        "get_delta",
        "get_delta_manager",
        "DeltaHelper",
        "get_redis",
        "RedisManager",
        "RedisHelper",
        "get_config",
        "Config",
        "is_valid_url",
        "is_uconn_domain",
    )

except ImportError as e:
    # If new modules don't exist yet, don't break existing code
    warnings.warn(f"Could not import from new modules: {e}", ImportWarning)
    __all__: tuple[str, ...] = ()
