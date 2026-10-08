"""#539: memory soft-stop drains the crawl (pipelines flush) before the OOM killer."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.memory_soft_stop import MemorySoftStop, cgroup_limit_bytes, usage_bytes

ROOT = Path(__file__).resolve().parents[2]


def _v2(tmp_path, limit, current):
    (tmp_path / "memory.max").write_text(str(limit))
    (tmp_path / "memory.current").write_text(str(current))
    return tmp_path


def test_cgroup_v2_v1_and_unlimited(tmp_path):
    v2 = tmp_path / "v2"
    v2.mkdir()
    assert cgroup_limit_bytes(_v2(v2, 2 * 2**30, 10)) == 2 * 2**30
    assert usage_bytes(v2) == 10
    (v2 / "memory.max").write_text("max\n")
    assert cgroup_limit_bytes(v2) is None

    v1 = tmp_path / "v1"
    (v1 / "memory").mkdir(parents=True)
    (v1 / "memory" / "memory.limit_in_bytes").write_text(str(512 * 2**20))
    (v1 / "memory" / "memory.usage_in_bytes").write_text("123")
    assert cgroup_limit_bytes(v1) == 512 * 2**20 and usage_bytes(v1) == 123
    (v1 / "memory" / "memory.limit_in_bytes").write_text(str(9223372036854771712))
    assert cgroup_limit_bytes(v1) is None

    none = tmp_path / "none"
    none.mkdir()
    assert cgroup_limit_bytes(none) is None
    assert usage_bytes(none) > 0  # falls back to process RSS


def _ext(root, **kw):
    engine = SimpleNamespace(calls=[])
    engine.close_spider = lambda spider, reason: engine.calls.append(reason)
    stats = SimpleNamespace(values={})
    stats.set_value = lambda k, v: stats.values.__setitem__(k, v)
    ext = MemorySoftStop(SimpleNamespace(engine=engine, stats=stats), root=root, **kw)
    ext._spider = object()
    return ext, engine, stats


def test_triggers_once_at_soft_limit(tmp_path):
    root = _v2(tmp_path, 1000, 800)
    ext, engine, stats = _ext(root)
    assert ext.check() is False and engine.calls == []  # 80% < 85%
    (root / "memory.current").write_text("900")
    assert ext.check() is True
    assert engine.calls == ["memory_soft_stop"]
    assert stats.values["memory_soft_stop/triggered"] == 1
    assert ext.check() is False and engine.calls == ["memory_soft_stop"]  # no double close


def test_override_limit_when_no_cgroup(tmp_path):
    ext, _, _ = _ext(tmp_path, limit_override_mb=64)
    assert ext.limit == 64 * 2**20


def test_not_configured_without_limit_or_when_disabled(tmp_path):
    from scrapy.exceptions import NotConfigured
    from scrapy.settings import Settings

    def crawler(**s):
        return SimpleNamespace(settings=Settings({"MEMORY_SOFT_STOP_CGROUP_ROOT": str(tmp_path), **s}),
                               signals=SimpleNamespace(connect=lambda *a, **k: None))

    with pytest.raises(NotConfigured):
        MemorySoftStop.from_crawler(crawler())
    with pytest.raises(NotConfigured):
        MemorySoftStop.from_crawler(crawler(MEMORY_SOFT_STOP_ENABLED=False, MEMORY_SOFT_STOP_LIMIT_MB=64))
    assert MemorySoftStop.from_crawler(crawler(MEMORY_SOFT_STOP_LIMIT_MB=64)).limit == 64 * 2**20


def test_registered_for_crawls_and_orchestrator():
    settings = (ROOT / "src" / "settings.py").read_text()
    orch = (ROOT / "src" / "orchestrator" / "pipeline_orchestrator.py").read_text()
    assert '"src.memory_soft_stop.MemorySoftStop": 520' in settings
    assert '"src.memory_soft_stop.MemorySoftStop": 520' in orch


CRAWL = textwrap.dedent('''
    import json, sys
    from pathlib import Path
    import scrapy
    from scrapy.crawler import CrawlerProcess

    cg = Path(sys.argv[1]); out = Path(sys.argv[2])

    class Batching:
        """Holds items in memory and only writes them in close_spider (like the queue/Kafka pipelines)."""
        def open_spider(self, spider):
            self.batch = []
        def process_item(self, item, spider):
            self.batch.append(item["n"])
            if len(self.batch) == 20:  # simulate memory pressure mid-crawl
                (cg / "memory.current").write_text(str(950 * 2**20))
            return item
        def close_spider(self, spider):
            out.write_text(json.dumps({"flushed": self.batch}))

    class Endless(scrapy.Spider):
        name = "endless"
        start_urls = ["data:,x"]
        n = 0
        def parse(self, response):
            for _ in range(5):
                Endless.n += 1
                yield {"n": Endless.n}
            yield scrapy.Request("data:,x", dont_filter=True, callback=self.parse)

    stats = {}
    def closed(spider, reason):
        stats["reason"] = reason
    p = CrawlerProcess({
        "LOG_LEVEL": "WARNING",
        "ITEM_PIPELINES": {"__main__.Batching": 100},
        "EXTENSIONS": {"src.memory_soft_stop.MemorySoftStop": 520},
        "MEMORY_SOFT_STOP_CGROUP_ROOT": str(cg),
        "MEMORY_SOFT_STOP_INTERVAL": 0.05,
        "TELNETCONSOLE_ENABLED": False,
        "CLOSESPIDER_TIMEOUT": 30,
    })
    crawler = p.create_crawler(Endless)
    crawler.signals.connect(closed, signal=scrapy.signals.spider_closed)
    p.crawl(crawler)
    p.start()
    data = json.loads(out.read_text())
    data["reason"] = stats.get("reason")
    out.write_text(json.dumps(data))
''')


def test_real_crawl_drains_and_flushes_on_soft_stop(tmp_path):
    cg = tmp_path / "cg"
    cg.mkdir()
    _v2(cg, 1000 * 2**20, 100 * 2**20)
    out = tmp_path / "out.json"
    script = tmp_path / "crawl.py"
    script.write_text(CRAWL)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")]))}
    proc = subprocess.run([sys.executable, str(script), str(cg), str(out)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    data = json.loads(out.read_text())
    assert data["reason"] == "memory_soft_stop"  # graceful close, not killed / timeout
    assert len(data["flushed"]) >= 20
    assert data["flushed"] == list(range(1, len(data["flushed"]) + 1))  # nothing processed was lost
