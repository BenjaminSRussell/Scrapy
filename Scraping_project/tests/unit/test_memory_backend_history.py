"""#484: InMemoryBackend time-travel history is bounded."""

import pytest

import src.lakehouse.lakehouse_manager as lm
from src.lakehouse.lakehouse_manager import InMemoryBackend


def test_history_is_capped_at_depth():
    backend = InMemoryBackend(history_depth=3)
    for i in range(50):
        backend.write("t", [{"i": i}])
    assert len(backend.history["t"]) == 3
    assert len(backend.get_table_history("t")) == 3


def test_versions_stay_absolute_and_evicted_ones_raise():
    backend = InMemoryBackend(history_depth=3)
    for i in range(5):
        backend.write("t", [{"i": i}])  # versions 0..4; 2..4 retained
    assert backend._get_version("t", 4) == [{"i": j} for j in range(5)]
    assert backend._get_version("t", 2) == [{"i": j} for j in range(3)]
    for gone in (0, 1, 5):
        with pytest.raises(ValueError):
            backend._get_version("t", gone)
    assert [h["version"] for h in backend.get_table_history("t")] == [2, 3, 4]


def test_read_with_version_uses_retained_snapshot():
    backend = InMemoryBackend(history_depth=2)
    backend.write("t", [{"i": 0}])
    backend.write("t", [{"i": 1}], mode="overwrite")
    backend.write("t", [{"i": 2}], mode="overwrite")
    assert backend.read("t", version=1) == [{"i": 1}]
    with pytest.raises(ValueError):
        backend.read("t", version=0)


def test_depth_from_config_and_default(monkeypatch):
    class _Cfg:
        def get(self, key, default=None):
            return 4 if key == "delta_lake.memory_history_depth" else default

    monkeypatch.setattr(lm.Config, "get_instance", classmethod(lambda cls: _Cfg()))
    assert InMemoryBackend().history_depth == 4

    class _Empty:
        def get(self, key, default=None):
            return default

    monkeypatch.setattr(lm.Config, "get_instance", classmethod(lambda cls: _Empty()))
    assert InMemoryBackend().history_depth == lm.MEMORY_HISTORY_DEPTH
    assert InMemoryBackend(history_depth=0).history_depth == 1
