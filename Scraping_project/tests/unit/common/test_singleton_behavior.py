from src.common.config import Config
from src.common.delta_lake import DeltaLakeManager

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
    manager1 = DeltaLakeManager.get_instance(start_workers=False)
    assert manager1._workers_started is False

    manager2 = DeltaLakeManager.get_instance(start_workers=True)
    assert manager2._workers_started is False

def test_delta_lake_manager_singleton_reset_allows_reinitialization():
    manager1 = DeltaLakeManager.get_instance(start_workers=False)
    assert manager1._workers_started is False

    DeltaLakeManager.reset_instance()

    manager2 = DeltaLakeManager.get_instance(start_workers=True)
    assert manager2._workers_started is True
