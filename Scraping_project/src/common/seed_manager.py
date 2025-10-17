"""Legacy facade for SeedManager - DEPRECATED.

⚠️  MIGRATION NOTICE:
This module is now a compatibility facade that imports from src.lakehouse.
All new code should import directly from src.lakehouse instead:

    # OLD (deprecated):
    from src.common.seed_manager import SeedManager

    # NEW (preferred):
    from src.lakehouse import SeedManager

This facade will be maintained for backward compatibility during the migration period,
but may be removed in a future version.
"""

# Import from the new lakehouse module
from src.lakehouse.seed_manager import SeedManager, create_seed_manager_from_delta, default_url_hasher

# Re-export for backward compatibility
__all__ = [
    "SeedManager",
    "default_url_hasher",
    "create_seed_manager_from_delta",
]
