from src.common.config import Config
from src.common.delta_lake import DeltaLakeManager


def test_config_singleton_persists_state():
    """Verify that the Config singleton maintains state across calls."""
    # Get the first instance and set a custom value
    config1 = Config.get_instance()
    config1.set("test.key", "test_value")

    # Get a second instance and check if the value is the same
    config2 = Config.get_instance()
    assert config2.get("test.key") == "test_value"


def test_config_singleton_reset():
    """Verify that resetting the Config singleton clears its state."""
    # Get an instance and set a value
    config1 = Config.get_instance()
    config1.set("test.key", "test_value")

    # Reset the singleton
    Config.reset_instance()

    # Get a new instance and check that the value is gone
    config2 = Config.get_instance()
    assert config2.get("test.key") is None


def test_delta_lake_manager_singleton_bug():
    """
    This test demonstrates the bug that the singleton fix addresses.
    Once the singleton is created, subsequent calls to get_instance() with
    different parameters are ignored.
    """
    # First call with start_workers=False
    manager1 = DeltaLakeManager.get_instance(start_workers=False)
    assert manager1._workers_started is False

    # Second call, attempting to start workers
    manager2 = DeltaLakeManager.get_instance(start_workers=True)
    # The bug is that this will still be False, because it returns the first instance
    assert manager2._workers_started is False


def test_delta_lake_manager_singleton_reset_allows_reinitialization():
    """
    Verify that after resetting, the DeltaLakeManager can be re-initialized
    with new parameters.
    """
    # First call with start_workers=False
    manager1 = DeltaLakeManager.get_instance(start_workers=False)
    assert manager1._workers_started is False

    # Reset the singleton
    DeltaLakeManager.reset_instance()

    # Second call after reset, with start_workers=True
    manager2 = DeltaLakeManager.get_instance(start_workers=True)
    assert manager2._workers_started is True
