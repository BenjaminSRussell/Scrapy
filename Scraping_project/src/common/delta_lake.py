"""Legacy facade for Delta Lake operations - DEPRECATED.

⚠️  MIGRATION NOTICE:
This module is now a compatibility facade that imports from src.lakehouse.
All new code should import directly from src.lakehouse instead:

    # OLD (deprecated):
    from src.common.delta_lake import DeltaLakeManager, get_delta_manager

    # NEW (preferred):
    from src.lakehouse import LakehouseManager, get_lakehouse_manager

This facade will be maintained for backward compatibility during the migration period,
but may be removed in a future version.

====================================================================================
MIGRATION GUIDE
====================================================================================

Phase 1 (CURRENT): Stabilization
- DeltaLakeManager has been enhanced with:
  - Missing shims (append_to_table, read_table, get_table_path)
  - Schema refresh logic to detect and handle new columns
  - Improved InMemoryDeltaManager with full merge_into support
- Default backend changed from 'memory' to 'lakehouse' for production safety

Phase 2: Namespace Migration
- Core implementation moved to src/lakehouse/lakehouse_manager.py
- DeltaLakeManager renamed to LakehouseManager (with alias for compatibility)
- This file (src/common/delta_lake.py) now acts as a facade

Phase 3 (TODO): Incremental Updates
- Update imports in spiders, pipelines, and tests
- Use the new lakehouse module directly
- Remove this facade once migration is complete

====================================================================================
"""

# Import everything from the new lakehouse module
from src.lakehouse.lakehouse_manager import (
    DELTA_AVAILABLE,
    DeltaLakeManager,  # Alias for LakehouseManager (backward compatibility)
    InMemoryBackend,  # New name
    LakehouseManager,
    delta_session,  # Alias for lakehouse_session (backward compatibility)
    get_delta_manager,  # Alias for get_lakehouse_manager (backward compatibility)
    get_lakehouse_manager,
    lakehouse_session,
)
from src.lakehouse.lakehouse_manager import (
    InMemoryBackend as InMemoryDeltaManager,  # Alias for backward compatibility
)

# Re-export for backward compatibility
__all__ = [
    "DeltaLakeManager",  # Legacy alias
    "LakehouseManager",  # New name
    "InMemoryDeltaManager",  # Legacy alias
    "InMemoryBackend",  # New name
    "get_delta_manager",  # Legacy alias
    "get_lakehouse_manager",  # New name
    "delta_session",  # Legacy alias
    "lakehouse_session",  # New name
    "DELTA_AVAILABLE",
]

# Import logging to add a deprecation warning when old names are used
import logging

logger = logging.getLogger(__name__)

# Note: We don't add a warning here because it would fire on every import.
# Instead, the code is structured so that both old and new names work seamlessly.
# Teams can migrate at their own pace.
