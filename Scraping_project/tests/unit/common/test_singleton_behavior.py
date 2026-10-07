from src.core.config import Config
from src.lakehouse.lakehouse_manager import DeltaLakeManager

def test_config_singleton_persists_state():
    config1 = Config.get_instance()
    config1.set("test.key", "test_value")

    config2 = Config.get_instance()
    assert config2.get("test.key") == "test_value"

def test_config_singleton_reset():
    config1 = Config.get_instance()
    config1.set("test.key", "test_value")

    Config.reset_instance()

    config2 = Config.get_instance()
    assert config2.get("test.key") is None

def test_delta_lake_manager_singleton_bug():
    # Other tests in the suite may have already created the singleton
    # (e.g. via get_instance() elsewhere); start from a known-clean state
    # rather than assuming this test runs first.
    DeltaLakeManager.reset_instance()

    manager1 = DeltaLakeManager.get_instance(start_workers=False)
    assert manager1._workers_started is False

    manager2 = DeltaLakeManager.get_instance(start_workers=True)
    assert manager2._workers_started is False

    DeltaLakeManager.reset_instance()

def test_delta_lake_manager_singleton_reset_allows_reinitialization():
    DeltaLakeManager.reset_instance()

    manager1 = DeltaLakeManager.get_instance(start_workers=False)
    assert manager1._workers_started is False

    DeltaLakeManager.reset_instance()

    manager2 = DeltaLakeManager.get_instance(start_workers=True)
    assert manager2._workers_started is True

    DeltaLakeManager.reset_instance()
