"""#466 correlation fields / #238 LOG_FORMAT=json; worker entrypoints actually log INFO."""

import io
import json
import logging
import sys

import pytest

from src.utils import logging_config as lc
from src.utils.logging_config import CorrelationFilter, configure_logging, log_context

pytestmark = pytest.mark.unit


@pytest.fixture
def bare_root(monkeypatch):
    """A root logger with no handlers (what a fresh `python -m ...` process has)."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    for h in saved_handlers:
        root.removeHandler(h)
    for var in ("LOG_FORMAT", "LOG_LEVEL", "WORKER_ID", "CRAWL_JOB_ID"):
        monkeypatch.delenv(var, raising=False)
    yield root
    for h in root.handlers[:]:
        root.removeHandler(h)
    for h in saved_handlers:
        for f in [f for f in h.filters if isinstance(f, CorrelationFilter)]:
            h.removeFilter(f)
        root.addHandler(h)
    root.setLevel(saved_level)


def _lines(stream):
    return [line for line in stream.getvalue().splitlines() if line]


def test_text_default_is_human_with_correlation_tag(bare_root):
    out = io.StringIO()
    configure_logging("stage2", worker_id="w-1", crawl_job_id="job-9", stream=out)
    logging.getLogger("src.stage2.x").info("fetched %d pages", 3)
    (line,) = _lines(out)
    assert "[INFO] src.stage2.x: fetched 3 pages [stage=stage2 worker=w-1 job=job-9]" in line
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)


def test_json_mode_one_object_per_line_with_fields(bare_root, monkeypatch):
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("CRAWL_JOB_ID", "env-job")
    out = io.StringIO()
    configure_logging("stage3", worker_id="w-2", stream=out)
    log = logging.getLogger("src.stage3.y")
    log.info("one")
    with log_context(url="https://a.edu/p", url_hash="abc123"):
        log.warning("two", extra={"rows": 5})
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("three")
    first, second, third = (json.loads(line) for line in _lines(out))
    assert first["message"] == "one" and first["level"] == "INFO" and first["logger"] == "src.stage3.y"
    assert first["stage"] == "stage3" and first["worker_id"] == "w-2" and first["crawl_job_id"] == "env-job"
    assert "url" not in first
    assert second["url"] == "https://a.edu/p" and second["url_hash"] == "abc123" and second["rows"] == 5
    assert "ValueError: boom" in third["exc_info"]


def test_crawl_job_id_resolution_order(bare_root, monkeypatch):
    out = io.StringIO()
    monkeypatch.setenv("CRAWL_JOB_ID", "env")
    monkeypatch.setattr(lc, "_otel_job_id", lambda: "otel")
    configure_logging("stage2", worker_id="w", fmt="json", stream=out)
    log = logging.getLogger("t")
    log.info("a")  # otel beats env
    with log_context(crawl_job_id="ctx"):
        log.info("b")  # context beats everything
    configure_logging("stage2", worker_id="w", crawl_job_id="arg", fmt="json", stream=out)
    log.info("c")  # explicit argument beats otel/env
    assert [json.loads(line)["crawl_job_id"] for line in _lines(out)] == ["otel", "ctx", "arg"]


def test_idempotent_and_keeps_foreign_handlers(bare_root):
    foreign = logging.StreamHandler(io.StringIO())
    bare_root.addHandler(foreign)
    configure_logging("cli", worker_id="w")
    configure_logging("cli", worker_id="w")
    # Like basicConfig: with Scrapy's/pytest's handlers present, don't add a second text handler.
    assert foreign in bare_root.handlers
    assert not any(getattr(h, lc._HANDLER_MARK, False) for h in bare_root.handlers)
    assert sum(isinstance(f, CorrelationFilter) for f in foreign.filters) == 1
    # Explicit JSON still installs exactly one handler of ours, however often it's called.
    configure_logging("cli", fmt="json")
    configure_logging("cli", fmt="json")
    assert sum(getattr(h, lc._HANDLER_MARK, False) for h in bare_root.handlers) == 1


def test_level_and_bad_format(bare_root, monkeypatch):
    out = io.StringIO()
    monkeypatch.setenv("LOG_LEVEL", "warning")
    configure_logging("stage4", stream=out)
    logging.getLogger("q").info("hidden")
    logging.getLogger("q").warning("shown")
    assert [line.split(": ", 1)[1].split(" [")[0] for line in _lines(out)] == ["shown"]
    with pytest.raises(ValueError):
        configure_logging("stage4", fmt="xml")


def test_json_output_is_still_redacted(bare_root):
    out = io.StringIO()
    configure_logging("stage2", fmt="json", stream=out)
    logging.getLogger("r").info("connecting to redis://user:hunter2secret@redis:6379/0")
    (line,) = _lines(out)
    assert "hunter2secret" not in line and "[REDACTED]" in json.loads(line)["message"]


ENTRYPOINT = """
import asyncio, logging, runpy, sys
import src.stage{n}.stage{n}_worker as mod

async def fake_run():
    logging.getLogger(mod.__name__).info("worker loop started")

mod.run_stage{n}_worker = fake_run
runpy.run_module("src.workers.stage{n}_worker", run_name="__main__")
"""


@pytest.mark.parametrize("stage", ["2", "3", "4"])
def test_worker_entrypoints_emit_info(stage):
    """A fresh `python -m src.workers.stageN_worker` process used to drop every INFO line."""
    import os
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "WORKER_ID": "pod-7", "PYTHONPATH": f"{root}{os.pathsep}{root / 'src'}"}
    env.pop("LOG_FORMAT", None)
    env.pop("LOG_LEVEL", None)
    res = subprocess.run(
        [sys.executable, "-c", ENTRYPOINT.format(n=stage)], cwd=root, env=env, capture_output=True, text=True, timeout=120
    )
    assert res.returncode == 0, res.stderr[-2000:]
    line = next((x for x in res.stderr.splitlines() if "worker loop started" in x), "")
    assert "[INFO]" in line and f"stage=stage{stage}" in line and "worker=pod-7" in line, res.stderr[-2000:]
