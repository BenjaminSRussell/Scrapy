"""
Global Redis utilities.

Centralizes all Redis operations to eliminate duplicate code across the pipeline.
Replaces src/common/redis_manager.py with a simpler, more consistent API.
"""

from src.utils.url_canon import canonical_or_raw
from typing import Any, Callable, Optional, Set, List, cast
import hashlib
import json
import os
import re
import redis
import logging
import threading
from functools import wraps
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Seen-URL store policy (#159, #163, #191)
#
# Keys: ``site:{site_id}:{key_prefix}:urls`` (a Redis SET). ``site_id`` is the
# explicit argument, else the id pinned on the helper, else SITE_ID, else
# TENANT_ID, else REDIS_SITE_ID, else ``default``. Two crawl profiles that
# share an id share a seen-set. The old unscoped keys (``seen:urls``,
# ``scout:urls``, ``{prefix}:urls``) are not read or written. See
# ``SEEN_KEY_MIGRATION``.
#
# Ownership: ``claim_url`` / ``claim_urls`` use one ``SADD`` per URL, which
# returns 1 only for the caller that inserted it. That single atomic op
# decides first-seen ownership; a separate SISMEMBER-then-SADD can't, because
# two workers can both see "absent" before either adds.
#
# Failure policy: when Redis errors, the seen helpers fail CLOSED by default.
# They raise ``SeenStoreUnavailable`` rather than reporting a URL as unseen
# (which would cause duplicate crawls) or silently not recording it (which
# would cause endless re-crawls). Callers should pause enqueueing and retry
# later. Set ``REDIS_SEEN_FAIL_MODE=open`` to restore the old behaviour
# (treat as unseen, log) for local debugging. Every error increments
# ``redis_seen_check_errors_total{op=...}`` either way.
# ---------------------------------------------------------------------------

SEEN_FAIL_MODE_ENV = "REDIS_SEEN_FAIL_MODE"

# Site/tenant segment on every seen-set key (#191). Not part of the Redis
# connection contract (REDIS_HOST / REDIS_PORT / REDIS_PASSWORD / REDIS_DB);
# REDIS_URL stays a connection fallback only and is never a key namespace.
SITE_ID_ENV = "SITE_ID"
TENANT_ID_ENV = "TENANT_ID"
REDIS_SITE_ID_ENV = "REDIS_SITE_ID"
DEFAULT_SITE_ID = "default"
_SITE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_BAD_PREFIX_CHARS = frozenset("\r\n\x00")

SEEN_KEY_MIGRATION = """
Migration from single-site seen keys (#191)
--------------------------------------------
Older releases stored dedupe sets at ``{prefix}:urls`` with no site segment
(``seen:urls``, ``scout:urls``, and whatever string callers passed as
key_prefix). RedisHelper no longer reads or writes those keys. Every seen-set
key is ``site:{site_id}:{prefix}:urls`` (the acceptance form ``site:{id}:seen:urls``
when the prefix is ``seen``; for example ``site:uconn:seen:urls``).

There is no automatic copy. Reading the old key when the new one is missing
would hide two sites that forgot to set an id and put them back on one set.
Before deploying this build, rename each existing set. RENAME replaces the
destination if it already exists, so rename only when the new key is absent
(EXISTS returns 0):

    RENAME seen:urls site:default:seen:urls
    RENAME scout:urls site:default:scout:urls
    # any other prefix P, with ":" in P left as-is:
    RENAME P:urls site:{SITE_ID}:P:urls

``default`` is the site id used when SITE_ID, TENANT_ID and REDIS_SITE_ID are
all unset, which is the single-site install. A second crawl profile must set
a different SITE_ID or TENANT_ID (or pass site_id= to RedisHelper). Two
profiles left on ``default`` still share one set. SITE_ID wins over TENANT_ID,
which wins over REDIS_SITE_ID. An explicit site_id argument or constructor
value wins over all three.
"""


def resolve_site_id(site_id: Optional[str] = None) -> str:
    """Explicit id, else SITE_ID, else TENANT_ID, else REDIS_SITE_ID, else ``default``."""
    if site_id is not None and str(site_id).strip():
        candidate = str(site_id).strip()
    else:
        candidate = ""
        for name in (SITE_ID_ENV, TENANT_ID_ENV, REDIS_SITE_ID_ENV):
            raw = os.getenv(name)
            if raw and raw.strip():
                candidate = raw.strip()
                break
        if not candidate:
            candidate = DEFAULT_SITE_ID
    if _SITE_ID_RE.fullmatch(candidate) is None:
        raise ValueError(
            f"site/tenant id {candidate!r} must match [A-Za-z0-9][A-Za-z0-9._-]{{0,127}} "
            "(no colon or whitespace). Seen-set keys are site:{id}:{prefix}:urls (#191)."
        )
    return candidate


# Prometheus/StatsD label (#192). Same configured id as seen-set keys.
# Not a URL, job id, or raw host. A value that fails the site-id pattern
# is not copied onto a series. No numeric cap is applied: none was measured.
METRIC_SITE_REJECTED = "rejected"


def metric_site_id() -> str:
    """Configured site id for a low-cardinality metric label (#192).

    ``resolve_site_id()`` only (explicit argument is not accepted here, so a
    caller cannot pass a URL or host). ``SITE_ID``, else ``TENANT_ID``, else
    ``REDIS_SITE_ID``, else ``default``. An id that fails the pattern becomes
    ``rejected``. Series are not dropped to enforce a site cap.
    """
    try:
        return resolve_site_id()
    except ValueError:
        return METRIC_SITE_REJECTED


def seen_set_key(key_prefix: str = "seen", site_id: Optional[str] = None) -> str:
    """The only seen-set key RedisHelper writes: ``site:{id}:{prefix}:urls``."""
    site = resolve_site_id(site_id)
    prefix = key_prefix.strip() if isinstance(key_prefix, str) else ""
    if not prefix:
        prefix = "seen"
    if any(ch in prefix for ch in _BAD_PREFIX_CHARS):
        raise ValueError("key_prefix must not contain control characters")
    return f"site:{site}:{prefix}:urls"

# ---------------------------------------------------------------------------
# Connection pool backpressure (#533)
#
# Each RedisHelper owns a bounded ``redis.BlockingConnectionPool``. A caller
# that finds every connection busy waits up to REDIS_POOL_TIMEOUT seconds,
# then gets ``redis.ConnectionError("No connection available")``. On the
# seen/claim paths that error goes through the fail-closed policy above:
# ``SeenStoreUnavailable`` is raised (admission pauses) and
# ``redis_pool_exhausted_total{op}`` is incremented. It is never treated as
# "unseen", so a burst can't turn pool exhaustion into a duplicate-crawl storm.
# Before this, the pool was redis-py's default, which is unbounded with no
# backpressure, so a burst opened connections until Redis hit maxclients.
# Sizing: see "Redis connection pool sizing" in the README.
# ---------------------------------------------------------------------------
MAX_CONNECTIONS_ENV = "REDIS_MAX_CONNECTIONS"
POOL_TIMEOUT_ENV = "REDIS_POOL_TIMEOUT"
DEFAULT_MAX_CONNECTIONS = 50
DEFAULT_POOL_TIMEOUT = 2.0
_POOL_EXHAUSTED_MARKER = "No connection available"


def _env_positive(name: str, default: float, cast_to: type) -> Any:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return cast_to(default)
    try:
        value = cast_to(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return cast_to(default)
    return value if value > 0 else cast_to(default)


def is_pool_exhausted(error: BaseException) -> bool:
    """True for redis-py's BlockingConnectionPool timeout error."""
    return isinstance(error, redis.ConnectionError) and _POOL_EXHAUSTED_MARKER in str(error)


class SeenStoreUnavailable(RuntimeError):
    """Redis seen-URL store is unreachable; admission must pause (fail-closed)."""


try:
    from prometheus_client import Counter as _PromCounter

    REDIS_SEEN_ERRORS: Any = _PromCounter(
        "redis_seen_check_errors_total",
        "Redis seen-URL store errors by operation (check/mark/claim).",
        ["op"],
    )
    REDIS_POOL_EXHAUSTED: Any = _PromCounter(
        "redis_pool_exhausted_total",
        "Redis operations that found every pooled connection busy for REDIS_POOL_TIMEOUT (#533).",
        ["op"],
    )
except Exception:  # prometheus_client missing or metric already registered
    REDIS_SEEN_ERRORS = None
    REDIS_POOL_EXHAUSTED = None


def seen_fail_mode() -> str:
    mode = os.getenv(SEEN_FAIL_MODE_ENV, "closed").strip().lower()
    return "open" if mode == "open" else "closed"


# ---------------------------------------------------------------------------
# Queue high/low watermark backpressure (#196)
#
# Before a producer pushes, compare the queue depth to the high watermark.
# Above it (and until the depth falls to the low watermark) the producer
# either blocks or sheds. Defaults match QueueBacklog in
# monitoring/alerting_rules.yml (`redis_queue_length` > 10000). Prometheus
# does not load monitoring/alerts.yml (#178); that file stays absent.
#
# Knobs (process env, not a new Redis connection setting):
# REDIS_QUEUE_HIGH_WATERMARK, REDIS_QUEUE_LOW_WATERMARK,
# REDIS_QUEUE_BACKPRESSURE_MODE (block|shed),
# REDIS_QUEUE_BACKPRESSURE_SLEEP_SECONDS,
# REDIS_QUEUE_BACKPRESSURE_MAX_BLOCK_SECONDS,
# REDIS_QUEUE_SHED_BELOW_PRIORITY.
# An unparsable value, a non-positive high watermark, or a negative low
# watermark keeps that knob's default. A low watermark of 0 resumes only when
# the queue is empty. ``block`` sleeps until the depth is at or under the low
# watermark, bounded by the max block. ``shed`` refuses priorities below
# REDIS_QUEUE_SHED_BELOW_PRIORITY (and pushes with no priority) immediately.
# Higher priorities still block. One exhausted block does not sleep again
# until the queue has drained.
# ---------------------------------------------------------------------------
HIGH_WATERMARK_ENV = "REDIS_QUEUE_HIGH_WATERMARK"
LOW_WATERMARK_ENV = "REDIS_QUEUE_LOW_WATERMARK"
BACKPRESSURE_MODE_ENV = "REDIS_QUEUE_BACKPRESSURE_MODE"
BACKPRESSURE_SLEEP_ENV = "REDIS_QUEUE_BACKPRESSURE_SLEEP_SECONDS"
BACKPRESSURE_MAX_BLOCK_ENV = "REDIS_QUEUE_BACKPRESSURE_MAX_BLOCK_SECONDS"
SHED_BELOW_PRIORITY_ENV = "REDIS_QUEUE_SHED_BELOW_PRIORITY"

DEFAULT_QUEUE_HIGH_WATERMARK = 10_000
DEFAULT_QUEUE_LOW_WATERMARK = 8_000
DEFAULT_BACKPRESSURE_MODE = "block"
DEFAULT_BACKPRESSURE_SLEEP = 0.5
DEFAULT_BACKPRESSURE_MAX_BLOCK = 5.0
DEFAULT_SHED_BELOW_PRIORITY = 50

try:
    from prometheus_client import Counter as _BackpressureCounter

    REDIS_QUEUE_BACKPRESSURE: Any = _BackpressureCounter(
        "redis_queue_backpressure_total",
        "Producer hits of Redis queue high-watermark backpressure (#196).",
        ["queue", "action"],
    )
except Exception:  # prometheus_client missing or metric already registered
    REDIS_QUEUE_BACKPRESSURE = None

# Queues that have crossed the high watermark and not yet fallen to the low one.
_pressure_state: dict[str, dict[str, bool]] = {}
_pressure_lock = threading.Lock()


def reset_queue_backpressure() -> None:
    """Drop hysteresis. Tests call this so one queue name cannot leak into the next."""
    with _pressure_lock:
        _pressure_state.clear()


def _env_int(name: str, default: int, *, minimum: Optional[int] = 1) -> int:
    """Parse an int env var. ``minimum`` is inclusive; None allows any int."""
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    if minimum is not None and value < minimum:
        logger.warning("Ignoring %s=%r below %s; using %s", name, raw, minimum, default)
        return default
    return value


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid %s=%r; using %s", name, raw, default)
        return default
    if value <= 0:
        logger.warning("Ignoring non-positive %s=%r; using %s", name, raw, default)
        return default
    return value


def queue_watermark_settings() -> dict[str, Any]:
    """High/low watermarks and the block-or-shed mode. Env wins over the defaults.

    Also exposed as ``src.settings.redis_queue_watermarks`` so Scrapy settings
    and the producer read one resolver. The low watermark is always strictly
    below the high one.
    """
    high = _env_int(HIGH_WATERMARK_ENV, DEFAULT_QUEUE_HIGH_WATERMARK, minimum=1)
    low = _env_int(LOW_WATERMARK_ENV, DEFAULT_QUEUE_LOW_WATERMARK, minimum=0)
    if low >= high:
        low = (high * 4) // 5
        if low >= high:
            low = high - 1
    mode_raw = os.getenv(BACKPRESSURE_MODE_ENV, DEFAULT_BACKPRESSURE_MODE)
    mode = str(mode_raw).strip().lower() if mode_raw is not None else DEFAULT_BACKPRESSURE_MODE
    if mode not in ("block", "shed"):
        logger.warning("Ignoring invalid %s=%r; using %s", BACKPRESSURE_MODE_ENV, mode_raw, DEFAULT_BACKPRESSURE_MODE)
        mode = DEFAULT_BACKPRESSURE_MODE
    return {
        "high": high,
        "low": low,
        "mode": mode,
        "sleep": _env_float(BACKPRESSURE_SLEEP_ENV, DEFAULT_BACKPRESSURE_SLEEP),
        "max_block": _env_float(BACKPRESSURE_MAX_BLOCK_ENV, DEFAULT_BACKPRESSURE_MAX_BLOCK),
        "shed_below": _env_int(SHED_BELOW_PRIORITY_ENV, DEFAULT_SHED_BELOW_PRIORITY, minimum=None),
    }


def _backpressure_inc(queue: str, action: str) -> None:
    if REDIS_QUEUE_BACKPRESSURE is not None:
        REDIS_QUEUE_BACKPRESSURE.labels(queue=queue, action=action).inc()


def admit_enqueue(
    queue: str,
    depth: int,
    *,
    priority: Optional[int] = None,
    depth_probe: Optional[Callable[[], int]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    clock: Optional[Callable[[], float]] = None,
) -> bool:
    """Return True if the producer may push one item onto ``queue``.

    ``depth`` is the depth the caller just observed (tests pass a mock; this
    function does not open a Redis connection). While the queue is above the
    high watermark, or still above the low watermark after a trip, a ``shed``
    mode refuses low-priority items and ``block`` mode sleeps until the probe
    reports the low watermark or the block budget runs out. A refused push
    increments ``redis_queue_backpressure_total{queue,action}``.
    """
    cfg = queue_watermark_settings()
    high = int(cfg["high"])
    low = int(cfg["low"])
    try:
        observed = int(depth)
    except (TypeError, ValueError):
        observed = 0
    with _pressure_lock:
        pressured = queue in _pressure_state
        over = observed > high or (pressured and observed > low)
        if not over:
            _pressure_state.pop(queue, None)
            return True
        first = not pressured
        state = _pressure_state.setdefault(queue, {"exhausted": False})
        low_priority = priority is None or int(priority) < int(cfg["shed_below"])
        if cfg["mode"] == "shed" and low_priority:
            decision = "shed"
        elif state["exhausted"]:
            decision = "shed"
        else:
            decision = "block"
    if first:
        logger.warning(
            "Redis queue %s depth %s is over the high watermark %s (low=%s, mode=%s); applying backpressure",
            queue, observed, high, low, cfg["mode"],
        )
    if decision == "shed":
        _backpressure_inc(queue, "shed")
        return False

    sleep = sleeper or time.sleep
    now = clock or time.monotonic
    probe = depth_probe or (lambda: observed)
    deadline = now() + float(cfg["max_block"])
    counted_block = False
    while True:
        try:
            current = int(probe())
        except Exception as exc:  # the caller's depth read failed; keep the last observation
            logger.debug("Queue depth probe failed for %s: %s", queue, exc)
            current = observed
        if current <= low:
            with _pressure_lock:
                _pressure_state.pop(queue, None)
            return True
        remaining = deadline - now()
        step = min(float(cfg["sleep"]), remaining)
        if step <= 0:
            with _pressure_lock:
                held = _pressure_state.get(queue)
                if held is not None:
                    held["exhausted"] = True
            _backpressure_inc(queue, "shed")
            return False
        if not counted_block:
            _backpressure_inc(queue, "block")
            counted_block = True
        sleep(step)


# #161: seen:* sets and queues have no TTL. Under an allkeys-* policy Redis
# evicts them silently at maxmemory, the seen set vanishes and the crawl starts
# over. Compose/Helm run volatile-lru (only TTL keys are evictable); this check
# makes a misconfigured server loud.
UNSAFE_EVICTION_POLICIES = ("allkeys-lru", "allkeys-lfu", "allkeys-random")


def check_eviction_policy(client: Any) -> Optional[str]:
    """Log an ERROR if the server may evict TTL-less keys. Returns the policy.

    Returns None when CONFIG is unavailable (e.g. disabled on managed Redis).
    """
    try:
        raw = (client.config_get("maxmemory-policy") or {}).get("maxmemory-policy")
    except Exception as exc:  # noqa: BLE001 - CONFIG may be renamed/disabled
        logger.debug(f"Could not read Redis maxmemory-policy: {exc}")
        return None
    policy = str(raw) if raw is not None else None
    if policy in UNSAFE_EVICTION_POLICIES:
        logger.error(
            f"Redis maxmemory-policy is {policy}: seen-URL sets and queues (no TTL) can be "
            "evicted silently under memory pressure, causing recrawl storms. "
            "Use volatile-lru or noeviction (#161)."
        )
    return policy


def _shared_circuit_keys(name: str, host: str) -> tuple[str, str]:
    """State key and failure-counter key for one ``(name, host)`` breaker.

    A digest keeps stage2 names and IPv6 hosts (both contain colons) from
    colliding. The state JSON carries the labels the exporter emits.
    """
    digest = hashlib.sha256(f"{name}\0{host}".encode("utf-8")).hexdigest()
    return f"cbstate:{digest}", f"cbfail:{digest}"


class RedisHelper:
    """Centralized Redis operations."""

    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        db: int = 0,
        password: Optional[str] = None,
        max_connections: Optional[int] = None,
        pool_timeout: Optional[float] = None,
        site_id: Optional[str] = None,
    ):
        """
        Initialize Redis helper.

        Args:
            host: Redis host address (default: REDIS_HOST env var, then localhost)
            port: Redis port number (default: REDIS_PORT env var, then 6379)
            db: Redis database number
            password: Optional Redis AUTH password
            site_id: Pinned site/tenant id for seen-set keys (#191). None
                resolves SITE_ID / TENANT_ID / REDIS_SITE_ID / ``default``
                on each call. See ``SEEN_KEY_MIGRATION``.
        """
        # #511: REDIS_HOST/REDIS_PORT/REDIS_PASSWORD/REDIS_DB (REDIS_URL as fallback),
        # resolved in one place; explicit arguments still win.
        from src.utils.redis_env import redis_settings

        env = redis_settings()
        self.host = host or env.host
        self.port = port or env.port
        self.db = db or env.db
        # #184: AUTH password comes from the caller, else REDIS_PASSWORD (Helm
        # secret / compose .env). Empty means no AUTH (local dev only).
        self.password = password or env.password
        self._client: Optional[redis.Redis] = None
        # #533: bounded pool; see the module comment for the policy.
        self.max_connections = int(max_connections) if max_connections else _env_positive(
            MAX_CONNECTIONS_ENV, DEFAULT_MAX_CONNECTIONS, int)
        self.pool_timeout = float(pool_timeout) if pool_timeout else _env_positive(
            POOL_TIMEOUT_ENV, DEFAULT_POOL_TIMEOUT, float)
        # None: resolve from the environment at each seen-set call (#191).
        pinned = str(site_id).strip() if site_id is not None else ""
        self.site_id: Optional[str] = pinned or None

    def seen_key(self, key_prefix: str = "seen", site_id: Optional[str] = None) -> str:
        """Seen-set key for this helper: ``site:{id}:{prefix}:urls`` (#191)."""
        if site_id is not None and str(site_id).strip():
            return seen_set_key(key_prefix, site_id=site_id)
        if self.site_id:
            return seen_set_key(key_prefix, site_id=self.site_id)
        return seen_set_key(key_prefix)

    @property
    def client(self) -> redis.Redis:
        """Lazy connection to Redis."""
        if self._client is None:
            try:
                conn_kwargs: dict[str, Any] = dict(
                    host=self.host,
                    port=self.port,
                    db=self.db,
                    password=self.password,
                    decode_responses=True,
                    socket_timeout=5,
                    socket_connect_timeout=5,
                )
                pool = redis.BlockingConnectionPool(
                    max_connections=self.max_connections,
                    timeout=self.pool_timeout,
                    **conn_kwargs,
                )
                # Connection kwargs are passed too (redis-py ignores them when
                # a pool is given) so construction stays inspectable.
                self._client = redis.Redis(connection_pool=pool, **conn_kwargs)
                self._client.ping()
                logger.info(f"Connected to Redis at {self.host}:{self.port}")
                check_eviction_policy(self._client)
                if not self.password:
                    logger.warning(
                        "Redis AUTH is not configured (REDIS_PASSWORD unset). "
                        "Acceptable for local dev only; set REDIS_PASSWORD in any shared environment."
                    )
            except redis.ConnectionError as e:
                logger.error(f"Failed to connect to Redis: {e}")
                raise
        return self._client

    def check_url_seen(self, url: str, key_prefix: str = "seen", *, site_id: Optional[str] = None) -> bool:
        """
        Check if URL has been seen before for this site.

        Args:
            url: URL to check
            key_prefix: Purpose segment of the key (default ``seen``)
            site_id: Overrides the helper/env site id for this call

        Returns:
            True if URL was seen before, False otherwise

        The Redis key is ``site:{id}:{key_prefix}:urls`` (#191).

        Example:
            redis = get_redis()
            if redis.check_url_seen("https://uconn.edu", "scout", site_id="uconn"):
                print("Already seen")
        """
        key = self.seen_key(key_prefix, site_id=site_id)
        try:
            return bool(self.client.sismember(key, canonical_or_raw(url)))  # #728
        except Exception as e:
            return bool(self._seen_store_error("check", e, fallback=False))

    def mark_url_seen(self, url: str, key_prefix: str = "seen", *, site_id: Optional[str] = None) -> bool:
        """
        Mark URL as seen for this site.

        Args:
            url: URL to mark
            key_prefix: Purpose segment of the key (default ``seen``)
            site_id: Overrides the helper/env site id for this call

        Returns:
            True if successful, False otherwise

        The Redis key is ``site:{id}:{key_prefix}:urls`` (#191).

        Example:
            redis = get_redis()
            redis.mark_url_seen("https://uconn.edu", "scout", site_id="uconn")
        """
        key = self.seen_key(key_prefix, site_id=site_id)
        try:
            self.client.sadd(key, canonical_or_raw(url))  # #728
            return True
        except Exception as e:
            return bool(self._seen_store_error("mark", e, fallback=False))

    def claim_url(self, url: str, key_prefix: str = "seen", *, site_id: Optional[str] = None) -> bool:
        """Atomically claim first-seen ownership of ``url`` for this site (#159, #191).

        Returns True for exactly one caller across all processes (the one whose
        SADD inserted the member); False if it was already seen. A different
        ``site_id`` is a different set. Raises ``SeenStoreUnavailable`` on Redis
        errors unless fail mode is ``open``. The key is ``site:{id}:{prefix}:urls``.
        """
        key = self.seen_key(key_prefix, site_id=site_id)
        try:
            return int(cast(int, self.client.sadd(key, canonical_or_raw(url)))) == 1
        except Exception as e:
            # Fail-open treats the URL as unseen, i.e. claimed (old behaviour).
            return bool(self._seen_store_error("claim", e, fallback=True))

    def claim_urls(
        self, urls: list[str], key_prefix: str = "seen", *, site_id: Optional[str] = None
    ) -> list[str]:
        """Claim many URLs in one round trip; returns those this caller now owns.

        Every URL in the batch is claimed in ``site:{id}:{prefix}:urls`` (#191).
        """
        if not urls:
            return []
        key = self.seen_key(key_prefix, site_id=site_id)
        try:
            pipe = self.client.pipeline(transaction=False)
            for url in urls:
                pipe.sadd(key, canonical_or_raw(url))  # #728: variants collide
            results = pipe.execute()
            return [u for u, added in zip(urls, results, strict=False) if int(added) == 1]
        except Exception as e:
            self._seen_store_error("claim", e, fallback=None)
            return list(urls)

    def _seen_store_error(self, op: str, error: Exception, fallback: Any) -> Any:
        """Apply the seen-store failure policy: count, then raise or fall back."""
        if REDIS_SEEN_ERRORS is not None:
            REDIS_SEEN_ERRORS.labels(op=op).inc()
        if is_pool_exhausted(error):
            if REDIS_POOL_EXHAUSTED is not None:
                REDIS_POOL_EXHAUSTED.labels(op=op).inc()
            logger.warning(
                "Redis pool exhausted during %s (%d connections busy for %.2fs)",
                op, self.max_connections, self.pool_timeout,
            )
        if seen_fail_mode() == "open":
            logger.error(f"Redis seen-store {op} failed; failing OPEN ({SEEN_FAIL_MODE_ENV}=open): {error}")
            return fallback
        logger.error(f"Redis seen-store {op} failed; failing CLOSED, pausing admission: {error}")
        raise SeenStoreUnavailable(f"Redis seen-URL store unavailable during {op}: {error}") from error

    def add_to_set(self, key: str, *values: str) -> int:
        """
        Add values to Redis set.

        Args:
            key: Redis key
            values: Values to add

        Returns:
            Number of values added
        """
        try:
            return cast(int, self.client.sadd(key, *values))
        except Exception as e:
            logger.error(f"Failed to add to set {key}: {e}")
            return 0

    def get_set_members(self, key: str) -> Set[str]:
        """
        Get all members of a Redis set.

        Args:
            key: Redis key

        Returns:
            Set of members
        """
        try:
            return cast(Set[str], self.client.smembers(key))
        except Exception as e:
            logger.error(f"Failed to get set members from {key}: {e}")
            return set()

    def get_set_size(self, key: str) -> int:
        """
        Get size of Redis set.

        Args:
            key: Redis key

        Returns:
            Number of members in set
        """
        try:
            return cast(int, self.client.scard(key))
        except Exception as e:
            logger.error(f"Failed to get set size for {key}: {e}")
            return 0

    def increment_counter(self, key: str, amount: int = 1) -> int:
        """
        Increment counter.

        Args:
            key: Redis key
            amount: Amount to increment by

        Returns:
            New counter value
        """
        try:
            return cast(int, self.client.incrby(key, amount))
        except Exception as e:
            logger.error(f"Failed to increment counter {key}: {e}")
            return 0

    def get_counter(self, key: str) -> int:
        """
        Get counter value.

        Args:
            key: Redis key

        Returns:
            Counter value, or 0 if not set
        """
        try:
            value = cast(Optional[str], self.client.get(key))
            return int(value) if value else 0
        except Exception as e:
            logger.error(f"Failed to get counter {key}: {e}")
            return 0

    def get_memory_usage(self) -> int:
        """
        Get Redis memory usage in bytes.

        Returns:
            Memory usage in bytes
        """
        try:
            info = cast(dict[str, Any], self.client.info("memory"))
            return int(info.get("used_memory", 0))
        except Exception as e:
            logger.error(f"Failed to get memory usage: {e}")
            return 0

    def get_key_count(self) -> int:
        """
        Get total number of keys in Redis.

        Returns:
            Number of keys
        """
        try:
            return cast(int, self.client.dbsize())
        except Exception as e:
            logger.error(f"Failed to get key count: {e}")
            return 0

    def delete_key(self, key: str) -> bool:
        """
        Delete a key from Redis.

        Args:
            key: Redis key to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            self.client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Failed to delete key {key}: {e}")
            return False

    def clear_all(self, confirm: bool = False) -> bool:
        """
        Clear all keys from current database.

        WARNING: This deletes ALL data in the current Redis database -- seen-URL
        sets, queues and counters shared by every worker (#203/#381).

        Refuses unless the caller passes ``confirm=True`` or the process runs
        with ``ALLOW_REDIS_FLUSH=1``, so a stray call cannot wipe the shared
        data plane.

        Returns:
            True if successful, False otherwise (including when refused)
        """
        if not confirm and os.getenv("ALLOW_REDIS_FLUSH") != "1":
            logger.error(
                "Refusing Redis flushdb: pass confirm=True or set ALLOW_REDIS_FLUSH=1"
            )
            return False
        try:
            self.client.flushdb()
            logger.warning("Cleared all keys from Redis database")
            return True
        except Exception as e:
            logger.error(f"Failed to clear Redis: {e}")
            return False

    def ping(self) -> bool:
        """
        Check if Redis is responsive.

        Returns:
            True if Redis responds, False otherwise
        """
        try:
            return bool(self.client.ping())
        except Exception as e:
            logger.error(f"Redis ping failed: {e}")
            return False

    def open_circuit(self, domain: str, duration_seconds: int = 900, reason: Optional[str] = None) -> bool:
        """
        Open the circuit breaker for a domain, blocking requests to it for a
        limited time.

        Args:
            domain: Domain to block
            duration_seconds: How long the circuit stays open
            reason: Optional human-readable reason, stored alongside the key

        Returns:
            True if successful, False otherwise
        """
        try:
            key = f"circuit:{domain}"
            value = json.dumps({"reason": reason, "opened_at": time.time()})
            self.client.set(key, value, ex=duration_seconds)
            return True
        except Exception as e:
            logger.error(f"Failed to open circuit for {domain}: {e}")
            return False

    def is_circuit_open(self, domain: str) -> bool:
        """
        Check whether the circuit breaker for a domain is currently open.

        Args:
            domain: Domain to check

        Returns:
            True if the circuit is open (requests should be skipped)
        """
        try:
            return cast(int, self.client.exists(f"circuit:{domain}")) > 0
        except Exception as e:
            logger.error(f"Failed to check circuit for {domain}: {e}")
            return False

    def get_open_circuits(self) -> List[str]:
        """
        List domains whose circuit breaker is currently open.

        Returns:
            List of domain names with an open circuit
        """
        try:
            keys = cast(List[str], self.client.keys("circuit:*"))
            return [key.split("circuit:", 1)[1] for key in keys]
        except Exception as e:
            logger.error(f"Failed to list open circuits: {e}")
            return []

    def incr_shared_circuit_failures(self, name: str, host: str, ttl_seconds: int) -> int:
        """Add one cluster-wide consecutive failure. The key expires with the breaker window."""
        _, fail_key = _shared_circuit_keys(name, host)
        count = int(cast(int, self.client.incr(fail_key)))
        self.client.expire(fail_key, max(int(ttl_seconds), 1))
        return count

    def read_shared_circuit(self, name: str, host: str) -> Optional[dict[str, Any]]:
        """Return the shared open/half-open record, or None if this breaker has none."""
        state_key, _ = _shared_circuit_keys(name, host)
        raw = self.client.get(state_key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(str(raw))
        except json.JSONDecodeError:
            logger.error(f"Ignoring corrupt shared circuit state at {state_key}")
            return None
        if not isinstance(data, dict):
            return None
        return cast(dict[str, Any], data)

    def write_shared_circuit(
        self,
        name: str,
        host: str,
        state: str,
        failure_count: int,
        opened_at: Optional[float],
        ttl_seconds: int,
    ) -> None:
        """Publish open or half-open state. TTL matches the recovery window so a dead worker cannot stick."""
        state_key, _ = _shared_circuit_keys(name, host)
        payload = json.dumps(
            {
                "name": name,
                "host": host,
                "state": state,
                "failure_count": int(failure_count),
                "opened_at": opened_at,
            }
        )
        self.client.set(state_key, payload, ex=max(int(ttl_seconds), 1))

    def clear_shared_circuit(self, name: str, host: str) -> None:
        state_key, fail_key = _shared_circuit_keys(name, host)
        self.client.delete(state_key, fail_key)

    def reset_shared_circuit_failures(self, name: str, host: str) -> None:
        """Drop the shared consecutive-failure count without touching an open state key."""
        _, fail_key = _shared_circuit_keys(name, host)
        self.client.delete(fail_key)

    def list_shared_circuits(self) -> List[dict[str, Any]]:
        """Open and half-open breakers published by ``CircuitBreaker`` (#171)."""
        keys = cast(List[Any], self.client.keys("cbstate:*"))
        rows: List[dict[str, Any]] = []
        for key in keys:
            raw = self.client.get(key)
            if not raw:
                continue
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                data = json.loads(str(raw))
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and "state" in data:
                rows.append(cast(dict[str, Any], data))
        return rows


# Global instance
_redis_helper: Optional[RedisHelper] = None


def get_redis(
    host: Optional[str] = None, port: Optional[int] = None, db: int = 0, password: Optional[str] = None
) -> RedisHelper:
    """
    Get global Redis helper instance.

    This is the primary way to access Redis operations throughout the pipeline.

    Args:
        host: Redis host address (default: REDIS_HOST env var, then localhost)
        port: Redis port number (default: REDIS_PORT env var, then 6379)
        db: Redis database number
        password: Optional Redis AUTH password

    Returns:
        RedisHelper instance

    Example:
        from src.utils.redis import get_redis

        redis = get_redis()
        if not redis.check_url_seen(url, "scout", site_id="uconn"):
            redis.mark_url_seen(url, "scout", site_id="uconn")
            # Process URL...
    """
    global _redis_helper
    if _redis_helper is None:
        _redis_helper = RedisHelper(host, port, db, password)
    return _redis_helper


def reset_redis():
    """Reset global Redis helper instance (useful for testing)."""
    global _redis_helper
    if _redis_helper and _redis_helper._client:
        _redis_helper._client.close()
    _redis_helper = None


def redis_retry(max_attempts: int = 3, backoff_factor: float = 2.0):
    """
    Decorator for Redis operations with retry logic.

    Args:
        max_attempts: Maximum number of retry attempts
        backoff_factor: Exponential backoff factor

    Example:
        @redis_retry(max_attempts=3)
        def my_redis_operation():
            redis = get_redis()
            return redis.client.get("mykey")
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except redis.ConnectionError as e:
                    if attempt == max_attempts - 1:
                        logger.error(f"Redis operation failed after {max_attempts} attempts: {e}")
                        raise
                    wait_time = backoff_factor ** attempt
                    logger.warning(f"Redis connection error, retrying in {wait_time}s...")
                    time.sleep(wait_time)
        return wrapper
    return decorator
