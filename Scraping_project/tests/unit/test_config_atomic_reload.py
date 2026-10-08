"""Config reloads are atomic snapshots (#590).

Covers torn reads under concurrent reload/set, a half-written file not
reverting the live config to defaults (wrong lake path), singleton creation
racing, copies not aliasing the live config, and the generation metric.
"""
import os
import threading
import time

import pytest

from src.core import config as config_mod
from src.core.config import Config, ConfigSnapshot, get_config, reset_config


def write_atomic(path, text):
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def pair_yaml(n):
    return (f"redis:\n  host: host-{n}\n  password: pw-{n}\n"
            f"delta_lake:\n  base_path: /lake/{n}\n")


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def test_no_torn_reads_under_concurrent_reload(tmp_path):
    path = tmp_path / "config.yml"
    write_atomic(path, pair_yaml(0))
    cfg = Config(path)
    stop = threading.Event()
    torn: list = []
    reads = [0]

    def reader():
        while not stop.is_set():
            snap = cfg.snapshot()
            host = snap.get("redis.host")
            pw = snap.get("redis.password")
            lake = snap.get("delta_lake.base_path")
            section = cfg.get_section("redis")
            if not (host.split("-")[1] == pw.split("-")[1] == lake.rsplit("/", 1)[1]):
                torn.append((host, pw, lake))
            if section["host"].split("-")[1] != section["password"].split("-")[1]:
                torn.append(("section", section))
            reads[0] += 1

    def reloader():
        for n in range(1, 40):
            write_atomic(path, pair_yaml(n))
            assert cfg.reload() is True

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    writers = [threading.Thread(target=reloader) for _ in range(2)]
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    for t in readers:
        t.join()

    assert not torn, torn[:3]
    assert reads[0] > 0
    assert cfg.generation == 1 + 2 * 39  # every reload swapped exactly once


def test_concurrent_set_never_exposes_a_half_applied_change(tmp_path):
    path = tmp_path / "config.yml"
    write_atomic(path, "a: {}\n")
    cfg = Config(path)
    stop = threading.Event()
    bad: list = []

    def reader():
        while not stop.is_set():
            snap = cfg.snapshot()
            x, y = snap.get("pair.x"), snap.get("pair.y")
            if (x is None) != (y is None) or (x is not None and x > y):
                bad.append((x, y))

    def writer():
        for i in range(300):
            # y is always set after x, so x > y in any snapshot means torn.
            with cfg._lock:
                cfg.set("pair.x", i)
                cfg.set("pair.y", i)

    rs = [threading.Thread(target=reader) for _ in range(3)]
    for t in rs:
        t.start()
    writer()
    stop.set()
    for t in rs:
        t.join()
    assert cfg.get("pair.x") == cfg.get("pair.y") == 299
    # x <= y holds in every snapshot because each set() swaps a whole copy.
    assert not bad, bad[:3]


def test_half_written_file_keeps_the_previous_snapshot(tmp_path):
    """Before #590, a reload that hit a truncated file silently reverted to
    the built-in defaults, so the lake path became ./data/delta_lake mid-run."""
    path = tmp_path / "config.yml"
    path.write_text(pair_yaml(7))
    cfg = Config(path)
    gen = cfg.generation
    path.write_text("delta_lake:\n  base_path: [/lake/8\nredis: {host: ")
    before = _failures()
    assert cfg.reload() is False
    assert cfg.get("delta_lake.base_path") == "/lake/7"
    assert cfg.get("redis.password") == "pw-7"
    assert cfg.generation == gen
    if before is not None:
        assert _failures() == before + 1


def test_vanished_or_non_mapping_file_keeps_the_previous_snapshot(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(pair_yaml(3))
    cfg = Config(path)
    path.write_text("- just\n- a list\n")
    assert cfg.reload() is False
    path.unlink()
    assert cfg.reload() is False
    assert cfg.get("redis.host") == "host-3"


def test_first_load_still_falls_back_to_defaults(tmp_path):
    cfg = Config(tmp_path / "missing.yml")
    assert cfg.get("delta_lake.base_path") == "./data/delta_lake"
    bad = tmp_path / "bad.yml"
    bad.write_text("redis: {host: ")
    assert Config(bad).get("delta_lake.base_path") == "./data/delta_lake"


def test_returned_values_are_copies(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(pair_yaml(1))
    cfg = Config(path)
    cfg.get_section("redis")["host"] = "evil"
    cfg.get("redis")["password"] = "evil"
    cfg.get_raw_config()["redis"]["host"] = "evil"
    assert cfg.get("redis.host") == "host-1"
    assert cfg.get("redis.password") == "pw-1"


def test_a_snapshot_is_frozen(tmp_path):
    path = tmp_path / "config.yml"
    path.write_text(pair_yaml(1))
    cfg = Config(path)
    snap = cfg.snapshot()
    write_atomic(path, pair_yaml(2))
    cfg.reload()
    assert snap.get("redis.host") == "host-1"
    assert cfg.get("redis.host") == "host-2"
    assert isinstance(snap, ConfigSnapshot)
    with pytest.raises(Exception):
        snap.generation = 99  # type: ignore[misc]


def test_singleton_creation_is_race_free(tmp_path, monkeypatch):
    real_init = Config.__init__

    def slow_init(self, *a, **kw):
        time.sleep(0.05)  # widen the check-then-create window
        real_init(self, *a, **kw)

    monkeypatch.setattr(Config, "__init__", slow_init)
    path = tmp_path / "config.yml"
    path.write_text(pair_yaml(1))
    barrier = threading.Barrier(12)
    got_global, got_cls = [], []

    def grab():
        barrier.wait()
        got_global.append(get_config(path))
        got_cls.append(Config.get_instance(path))

    ts = [threading.Thread(target=grab) for _ in range(12)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len({id(c) for c in got_global}) == 1
    assert len({id(c) for c in got_cls}) == 1


def _failures():
    try:
        from prometheus_client import REGISTRY
    except Exception:
        return None
    return REGISTRY.get_sample_value("scrapy_config_reload_failures_total")


def test_generation_is_exported_as_a_metric(tmp_path):
    if config_mod.CONFIG_GENERATION is None:
        pytest.skip("prometheus_client unavailable")
    from prometheus_client import REGISTRY

    path = tmp_path / "config.yml"
    path.write_text(pair_yaml(1))
    cfg = Config(path)
    write_atomic(path, pair_yaml(2))
    cfg.reload()
    cfg.set("redis.db", 3)
    assert cfg.generation == 3
    assert REGISTRY.get_sample_value("scrapy_config_generation") == 3
