"""
Connection pooling for HTTP and database connections.

Phase 8: Performance optimization through efficient resource management.
"""

import asyncio
import logging
from typing import Optional, Any, Callable, TypeVar, Generic
from datetime import datetime, timedelta
from contextlib import asynccontextmanager

from src.core.exceptions import (
    PoolExhausted,
    ConnectionPoolTimeout,
    ResourceExhaustedError
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


class ConnectionPool(Generic[T]):
    """
    Generic async connection pool with health checking.
    
    Manages a pool of reusable connections to avoid connection overhead.
    """

    def __init__(
        self,
        factory: Callable[[], T],
        min_size: int = 5,
        max_size: int = 20,
        timeout: float = 30.0,
        max_lifetime: Optional[float] = 3600.0,
        health_check: Optional[Callable[[T], bool]] = None
    ):
        self.factory = factory
        self.min_size = min_size
        self.max_size = max_size
        self.timeout = timeout
        self.max_lifetime = max_lifetime
        self.health_check = health_check

        self._pool: asyncio.Queue[tuple[T, datetime]] = asyncio.Queue(maxsize=max_size)
        self._size = 0
        self._lock = asyncio.Lock()
        self._initialized = False

        # Metrics
        self.metrics = {
            "total_connections": 0,
            "active_connections": 0,
            "pool_hits": 0,
            "pool_misses": 0,
            "health_check_failures": 0,
            "timeouts": 0
        }

    async def initialize(self):
        """Initialize pool with minimum connections."""
        if self._initialized:
            return

        async with self._lock:
            for _ in range(self.min_size):
                conn = await self._create_connection()
                await self._pool.put((conn, datetime.now()))
            self._initialized = True
            logger.info(f"Connection pool initialized with {self.min_size} connections")

    async def _create_connection(self) -> T:
        """Create a new connection."""
        if self._size >= self.max_size:
            raise PoolExhausted(f"Connection pool exhausted (max: {self.max_size})")

        try:
            conn = self.factory()
            self._size += 1
            self.metrics["total_connections"] += 1
            logger.debug(f"Created new connection (pool size: {self._size})")
            return conn
        except Exception as e:
            logger.error(f"Failed to create connection: {e}")
            raise

    async def _is_connection_valid(self, conn: T, created_at: datetime) -> bool:
        """Check if connection is still valid."""
        # Check age
        if self.max_lifetime:
            age = (datetime.now() - created_at).total_seconds()
            if age > self.max_lifetime:
                logger.debug("Connection expired due to max lifetime")
                return False

        # Health check
        if self.health_check:
            try:
                if not self.health_check(conn):
                    self.metrics["health_check_failures"] += 1
                    logger.debug("Connection failed health check")
                    return False
            except Exception as e:
                logger.warning(f"Health check error: {e}")
                self.metrics["health_check_failures"] += 1
                return False

        return True

    @asynccontextmanager
    async def acquire(self):
        """
        Acquire connection from pool.
        
        Usage:
            async with pool.acquire() as conn:
                result = await conn.execute(query)
        """
        if not self._initialized:
            await self.initialize()

        conn = None
        created_at = None

        try:
            # Try to get from pool
            try:
                conn, created_at = await asyncio.wait_for(
                    self._pool.get(),
                    timeout=self.timeout
                )
                self.metrics["pool_hits"] += 1

                # Validate connection
                if not await self._is_connection_valid(conn, created_at):
                    # Connection invalid, create new one
                    self._size -= 1
                    conn = await self._create_connection()
                    created_at = datetime.now()
                    self.metrics["pool_misses"] += 1

            except asyncio.TimeoutError:
                # Pool empty, try to create new connection
                self.metrics["timeouts"] += 1
                conn = await self._create_connection()
                created_at = datetime.now()
                self.metrics["pool_misses"] += 1

            self.metrics["active_connections"] += 1
            yield conn

        finally:
            # Return connection to pool
            if conn is not None:
                self.metrics["active_connections"] -= 1
                try:
                    await self._pool.put((conn, created_at))
                except asyncio.QueueFull:
                    # Pool full, discard connection
                    self._size -= 1
                    logger.debug("Pool full, discarding connection")

    async def close(self):
        """Close all connections in pool."""
        logger.info(f"Closing connection pool ({self._size} connections)")
        
        while not self._pool.empty():
            try:
                conn, _ = await self._pool.get_nowait()
                # Call close if connection has close method
                if hasattr(conn, 'close'):
                    try:
                        await conn.close()
                    except Exception as e:
                        logger.warning(f"Error closing connection: {e}")
                self._size -= 1
            except asyncio.QueueEmpty:
                break

        self._initialized = False
        logger.info("Connection pool closed")

    def get_stats(self) -> dict:
        """Get pool statistics."""
        hit_rate = (
            self.metrics["pool_hits"] / 
            (self.metrics["pool_hits"] + self.metrics["pool_misses"])
            if (self.metrics["pool_hits"] + self.metrics["pool_misses"]) > 0
            else 0
        )

        return {
            **self.metrics,
            "pool_size": self._size,
            "max_size": self.max_size,
            "hit_rate": hit_rate,
            "queue_size": self._pool.qsize()
        }


class HTTPConnectionPool:
    """HTTP connection pool using aiohttp."""

    def __init__(
        self,
        max_connections: int = 100,
        max_per_host: int = 10,
        timeout: float = 30.0
    ):
        import aiohttp
        
        self.connector = aiohttp.TCPConnector(
            limit=max_connections,
            limit_per_host=max_per_host,
            ttl_dns_cache=300
        )
        
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> Any:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            import aiohttp
            self._session = aiohttp.ClientSession(
                connector=self.connector,
                timeout=self.timeout
            )
        return self._session

    async def close(self):
        """Close session and connector."""
        if self._session and not self._session.closed:
            await self._session.close()
            await self.connector.close()


# Global HTTP pool
_http_pool: Optional[HTTPConnectionPool] = None


def get_http_pool() -> HTTPConnectionPool:
    """Get global HTTP connection pool."""
    global _http_pool
    if _http_pool is None:
        _http_pool = HTTPConnectionPool()
    return _http_pool
