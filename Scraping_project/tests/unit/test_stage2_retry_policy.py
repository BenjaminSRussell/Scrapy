"""#160: Stage 2 failures must not flip queue rows to completed."""

import pytest

from src.stage2.stage2_worker import Stage2Worker, plan_queue_updates
from src.utils.dead_letter_queue import DeadLetterQueue


def _ok(url):
    return {"url": url, "url_hash": "h", "has_error": False, "is_low_quality": False}


def _err(url, code=0, msg="timeout"):
    return {"url": url, "url_hash": "h", "has_error": True, "error_code": code, "error_message": msg}


def test_plan_errors_stay_pending_until_cap():
    prior = {}
    done, failed, retry = plan_queue_updates([_ok("a"), _err("b")], prior, max_retries=3)
    assert done == ["a"] and failed == [] and [r["url"] for r in retry] == ["b"]
    plan_queue_updates([_err("b")], prior, 3)
    done, failed, retry = plan_queue_updates([_err("b")], prior, 3)
    assert failed == ["b"] and retry == [] and prior["b"] == 3


@pytest.mark.parametrize("row", [_err("x", 404, "http_error"), _err("x", 0, "invalid_url")])
def test_plan_terminal_errors_fail_immediately(row):
    _, failed, retry = plan_queue_updates([row], {}, max_retries=3)
    assert failed == ["x"] and retry == []


@pytest.mark.parametrize("code", [408, 429, 503])
def test_plan_transient_http_errors_retry(code):
    _, failed, retry = plan_queue_updates([_err("x", code, "http_error")], {}, max_retries=3)
    assert failed == [] and len(retry) == 1


class FakeDelta:
    def __init__(self, urls):
        self.tables = {"stage2_queue": [{"url": u, "url_hash": "h", "status": "pending"} for u in urls]}

    def read_table(self, name):
        if name not in self.tables:
            raise FileNotFoundError(name)
        return [dict(r) for r in self.tables[name]]

    def write(self, name, rows, mode="append", async_write=True):
        self.tables.setdefault(name, []).extend(dict(r) for r in rows)

    def merge_into(self, name, rows, merge_key, update_columns):  # analysis upsert (#311)
        table = self.tables.setdefault(name, [])
        keys = {r[merge_key]: i for i, r in enumerate(table)}
        for r in rows:
            if r[merge_key] in keys:
                table[keys[r[merge_key]]].update(r)
            else:
                table.append(dict(r))
        return len(rows)


def _worker(monkeypatch, tmp_path, delta, outcomes):
    w = Stage2Worker.__new__(Stage2Worker)
    w.max_concurrent, w.batch_size, w.postgres = 5, 10, None
    w.delta = delta
    w.max_retries = 2
    w._dlq = DeadLetterQueue(tmp_path / "dlq")

    async def analyze(record):
        return outcomes[record["url"]].pop(0)

    async def update(urls, table_name="stage2_queue", status="completed"):
        for row in delta.tables[table_name]:
            if row["url"] in urls:
                row["status"] = status

    monkeypatch.setattr(w, "_analyze_url", analyze)
    monkeypatch.setattr(w, "_update_queue_status", update)
    return w


def _status(delta):
    return {r["url"]: r["status"] for r in delta.tables["stage2_queue"]}


@pytest.mark.asyncio
async def test_timeout_once_stays_pending_then_completes(monkeypatch, tmp_path):
    delta = FakeDelta(["u1"])
    w = _worker(monkeypatch, tmp_path, delta, {"u1": [_err("u1"), _ok("u1")]})
    await w._run_traced()
    assert _status(delta) == {"u1": "pending"}
    assert len(delta.tables["stage2_errors"]) == 1
    await w._run_traced()
    assert _status(delta) == {"u1": "completed"}


@pytest.mark.asyncio
async def test_max_retries_marks_failed_and_writes_dlq(monkeypatch, tmp_path):
    delta = FakeDelta(["u2"])
    w = _worker(monkeypatch, tmp_path, delta, {"u2": [_err("u2"), _err("u2")]})
    await w._run_traced()
    assert _status(delta) == {"u2": "pending"}
    await w._run_traced()  # 2nd failure, counted from stage2_errors history
    assert _status(delta) == {"u2": "failed"}
    entries = w._dlq.list_failed(stage="stage2")
    assert len(entries) == 1 and entries[0]["url"] == "u2" and entries[0]["retry_count"] == 2


def test_max_retries_env(monkeypatch):
    monkeypatch.setenv("STAGE2_MAX_RETRIES", "5")
    assert Stage2Worker().max_retries == 5
    monkeypatch.setenv("STAGE2_MAX_RETRIES", "junk")
    assert Stage2Worker().max_retries == 3
