"""#200: Stage 2 reuses one pooled aiohttp session per run."""

import asyncio

import aiohttp
from aiohttp import web

from src.stage2 import stage2_worker as s2

HTML = "<html><head><title>T</title></head><body>" + "<p>word " * 120 + "</p></body></html>"


class _FakeDelta:
    def __init__(self, queue):
        self.queue = queue
        self.writes = []

    def read_table(self, name, **kwargs):
        return list(self.queue) if name == "stage2_queue" else []

    def write(self, table, rows, mode="append", async_write=True):
        self.writes.append((table, list(rows)))
        return True


async def _page(request):
    return web.Response(text=HTML, content_type="text/html")


async def _serve():
    app = web.Application()
    app.router.add_get("/{n}", _page)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, port


def test_one_session_for_whole_run_and_closed_after(monkeypatch):
    created: list[aiohttp.ClientSession] = []
    real = aiohttp.ClientSession

    def counting(*a, **k):
        sess = real(*a, **k)
        created.append(sess)
        return sess

    monkeypatch.setattr(s2.aiohttp, "ClientSession", counting)

    async def scenario():
        runner, port = await _serve()
        try:
            queue = [{"url": f"http://127.0.0.1:{port}/{i}", "url_hash": f"h{i}", "status": "pending"} for i in range(7)]
            w = s2.Stage2Worker.__new__(s2.Stage2Worker)
            w.max_concurrent, w.batch_size = 3, 3  # 3 batches
            w.semaphore = asyncio.Semaphore(3)
            w.delta = _FakeDelta(queue)
            w.postgres = None
            w.max_retries = 3
            w._dlq = None
            w._session = None
            w.MIN_WORD_COUNT, w.MIN_TEXT_TO_HTML_RATIO, w.MASSIVE_DOC_THRESHOLD = 50, 0.1, 50000

            async def noop(*a, **k):
                return None

            w._update_queue_status = noop
            counts = await w.run()
            return w, counts
        finally:
            await runner.cleanup()

    w, counts = asyncio.run(scenario())
    assert counts["analyzed"] == 7 and counts["errors"] == 0
    assert len(created) == 1, "expected one ClientSession for all 7 URLs / 3 batches"
    assert created[0].closed and w._session is None
    assert created[0].connector is None or created[0].connector.closed


def test_connector_limit_matches_concurrency():
    async def scenario():
        w = s2.Stage2Worker.__new__(s2.Stage2Worker)
        w.max_concurrent = 7
        sess = w._new_session()
        try:
            return sess.connector.limit, sess.connector.limit_per_host
        finally:
            await sess.close()

    assert asyncio.run(scenario()) == (7, 7)
