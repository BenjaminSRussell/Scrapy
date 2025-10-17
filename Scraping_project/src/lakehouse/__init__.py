"""
Lakehouse module - Modern Delta Lake management architecture.

This module provides a clean, well-architected interface for Delta Lake operations,
replacing the legacy delta_lake.py module.

Key components:
- LakehouseManager: Main class for Delta Lake operations (formerly DeltaLakeManager)
- InMemoryBackend: Testing backend (formerly InMemoryDeltaManager)
- SeedManager: Centralized seeding and queueing logic
- Factory functions: get_lakehouse_manager(), lakehouse_session()

Migration notes:
- DeltaLakeManager is now LakehouseManager
- All functionality remains the same, just better organized
- Legacy imports from src.common.delta_lake still work via facade
"""

from src.lakehouse.lakehouse_manager import (
    InMemoryBackend,
    LakehouseManager,
    get_lakehouse_manager,
    lakehouse_session,
)
from src.lakehouse.seed_manager import SeedManager

__all__ = [
    "LakehouseManager",
    "InMemoryBackend",
    "SeedManager",
    "get_lakehouse_manager",
    "lakehouse_session",
]
