from src.lakehouse.lakehouse_manager import (
    DELTA_AVAILABLE,
    DeltaLakeManager,
    InMemoryBackend,
    LakehouseManager,
    delta_session,
    get_delta_manager,
    get_lakehouse_manager,
    lakehouse_session,
)
from src.lakehouse.lakehouse_manager import (
    InMemoryBackend as InMemoryDeltaManager,
)

__all__ = [
    "DeltaLakeManager",
    "LakehouseManager",
    "InMemoryDeltaManager",
    "InMemoryBackend",
    "get_delta_manager",
    "get_lakehouse_manager",
    "delta_session",
    "lakehouse_session",
    "DELTA_AVAILABLE",
]

# Import logging to add a deprecation warning when old names are used
import logging

logger = logging.getLogger(__name__)

# Note: We don't add a warning here because it would fire on every import.
