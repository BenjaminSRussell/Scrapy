"""#260: ConnectionPool reuse, growth, exhaustion, validation and close. No network."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

import pytest

from src.core.exceptions import PoolExhausted
from src.utils.connection_pool import ConnectionPool, HTTPConnectionPool


class Conn:
    created = 0

    def __init__(self):
        Conn.created += 1
        self.id = Conn.created
        self.closed = False
        self.healthy = True

    def close(self):  # sync close (most DB drivers)
        self.closed = True


class AsyncConn(Conn):
    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _reset():
    Conn.created = 0


def run(coro):
    return asyncio.run(coro)


def test_initialize_creates_min_size_and_reuses_connections():
    async def go():
        pool = ConnectionPool(Conn, min_size=2, max_size=4, timeout=1)
        async with pool.acquire() as a:
            pass
        async with pool.acquire() as b:
            pass
        return pool, a, b

    pool, a, b = run(go())
    stats = pool.get_stats()
    assert stats["pool_size"] == 2 and Conn.created == 2
    assert stats["pool_hits"] == 2 and stats["pool_misses"] == 0
    assert a.id in (1, 2) and b.id in (1, 2)
    assert stats["active_connections"] == 0


def test_grows_immediately_when_all_busy_and_below_max():
    # Used to block for the full ``timeout`` before creating a connection.
    async def go():
        pool = ConnectionPool(Conn, min_size=1, max_size=3, timeout=5)
        start = time.monotonic()
        async with pool.acquire() as a, pool.acquire() as b, pool.acquire() as c:
            elapsed = time.monotonic() - start
            ids = {a.id, b.id, c.id}
        return pool, elapsed, ids

    pool, elapsed, ids = run(go())
    assert elapsed < 1.0
    assert ids == {1, 2, 3}
    assert pool.get_stats()["pool_size"] == 3


def test_exhaustion_waits_for_timeout_then_raises_pool_exhausted():
    async def go():
        pool = ConnectionPool(Conn, min_size=1, max_size=1, timeout=0.2)
        async with pool.acquire():
            start = time.monotonic()
            with pytest.raises(PoolExhausted):
                async with pool.acquire():
                    pass
            return pool, time.monotonic() - start

    pool, waited = run(go())
    assert 0.15 <= waited < 2
    assert pool.get_stats()["timeouts"] == 1
    assert pool.get_stats()["active_connections"] == 0


def test_waiter_gets_a_connection_released_within_timeout():
    async def go():
        pool = ConnectionPool(Conn, min_size=1, max_size=1, timeout=2)

        async def holder():
            async with pool.acquire():
                await asyncio.sleep(0.1)

        task = asyncio.create_task(holder())
        await asyncio.sleep(0.01)
        async with pool.acquire() as conn:
            got = conn.id
        await task
        return got

    assert run(go()) == 1  # same connection handed over, no PoolExhausted


@pytest.mark.parametrize("kind", ["expired", "unhealthy", "health_check_raises"])
def test_invalid_connections_are_closed_and_replaced(kind):
    def health(conn):
        if kind == "health_check_raises":
            raise RuntimeError("ping failed")
        return conn.healthy

    async def go():
        pool = ConnectionPool(Conn, min_size=1, max_size=2, timeout=1, max_lifetime=60, health_check=health)
        await pool.initialize()
        conn, created = pool._pool.get_nowait()
        if kind == "expired":
            created = datetime.now() - timedelta(seconds=120)
        elif kind == "unhealthy":
            conn.healthy = False
        pool._pool.put_nowait((conn, created))
        async with pool.acquire() as fresh:
            pass
        return pool, conn, fresh

    pool, stale, fresh = run(go())
    assert stale.closed is True  # used to be dropped without close()
    assert fresh is not stale
    assert pool.get_stats()["pool_size"] == 1
    if kind != "expired":
        assert pool.get_stats()["health_check_failures"] == 1


@pytest.mark.parametrize("cls", [Conn, AsyncConn])
def test_close_handles_sync_and_async_close(cls):
    async def go():
        pool = ConnectionPool(cls, min_size=3, max_size=3, timeout=1)
        await pool.initialize()
        conns = [pool._pool.get_nowait()[0] for _ in range(3)]
        for c in conns:
            pool._pool.put_nowait((c, datetime.now()))
        await pool.close()
        return pool, conns

    pool, conns = run(go())
    assert all(c.closed for c in conns)
    assert pool.get_stats()["pool_size"] == 0


def test_factory_failure_propagates_and_does_not_leak_size():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) > 1:
            raise ConnectionError("db down")
        return Conn()

    async def go():
        pool = ConnectionPool(flaky, min_size=1, max_size=3, timeout=1)
        async with pool.acquire():
            with pytest.raises(ConnectionError):
                async with pool.acquire():
                    pass
        return pool

    pool = run(go())
    assert pool.get_stats()["pool_size"] == 1


def test_http_pool_session_is_lazy_and_reusable_after_close():
    async def go():
        pool = HTTPConnectionPool(max_connections=5, max_per_host=2, timeout=3)
        assert pool.connector is None  # nothing created outside a running loop
        first = await pool.get_session()
        assert first is await pool.get_session()
        assert pool.connector.limit == 5 and pool.connector.limit_per_host == 2
        await pool.close()
        second = await pool.get_session()  # used to fail: closed connector reused
        assert second is not first and not second.closed
        await pool.close()

    run(go())
