#!/usr/bin/env python3
"""Hashed API keys for Control Center control routes (#658).

``mint`` prints the bearer token once on stdout. Redis and Postgres store only
the SHA-256 hex digest. Postgres revocation is an insert into
``cc_api_key_revocations`` because the app role may SELECT/INSERT only (#538).

``CC_API_KEY_BACKEND`` is ``redis`` (default), ``postgres``, or ``both``.
Redis uses ``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_PASSWORD`` / ``REDIS_DB``.
This module does not read ``REDIS_URL``.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import secrets
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger("dashboard.cc_auth")

_TOKEN_RE = re.compile(r"^cc_[A-Za-z0-9_-]{40,80}$")
_TOKEN_IN_TEXT = re.compile(r"cc_[A-Za-z0-9_-]{40,}")
_KEY_ID_RE = re.compile(r"^ck_[0-9a-f]{16}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_PLAINTEXT: set[str] = set()

INSERT_KEY_SQL = (
    "INSERT INTO cc_api_keys (key_id, name, prefix, token_hash, created_at) "
    "VALUES (%s, %s, %s, %s, %s)"
)
SELECT_ID_SQL = "SELECT key_id FROM cc_api_keys WHERE key_id = %s"
INSERT_REVOCATION_SQL = (
    "INSERT INTO cc_api_key_revocations (key_id, revoked_at) VALUES (%s, %s) "
    "ON CONFLICT (key_id) DO NOTHING"
)
LOOKUP_SQL = (
    "SELECT k.key_id, k.name, k.prefix, k.token_hash, k.created_at, r.revoked_at "
    "FROM cc_api_keys k "
    "LEFT JOIN cc_api_key_revocations r ON r.key_id = k.key_id "
    "WHERE k.token_hash = %s"
)


class KeyStoreError(Exception):
    """Store failure whose message is safe to print (no secrets, no DSN)."""


class KeyNotFound(KeyStoreError):
    pass


def scrub(text: str) -> str:
    """Drop minted tokens and anything shaped like a control-plane key."""
    if not text:
        return text
    for secret in list(_PLAINTEXT):
        if secret and secret in text:
            text = text.replace(secret, "[REDACTED]")
    return _TOKEN_IN_TEXT.sub("[REDACTED]", text)


class _PlaintextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        scrubbed = scrub(message)
        if scrubbed != message:
            record.msg = scrubbed
            record.args = ()
        if record.exc_info and not record.exc_text:
            record.exc_text = scrub("".join(traceback.format_exception(*record.exc_info)).rstrip("\n"))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = scrub(record.exc_text)
        return True


def install_log_scrub(target: logging.Logger | None = None) -> None:
    """Attach the plaintext scrubber to one logger (not the root logger)."""
    target = logger if target is None else target
    if not any(isinstance(existing, _PlaintextFilter) for existing in target.filters):
        target.addFilter(_PlaintextFilter())


def remember_plaintext(token: str) -> None:
    """Keep a minted or accepted token so a later log line cannot repeat it."""
    if token:
        _PLAINTEXT.add(token)


install_log_scrub(logger)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def validate_name(name: str) -> str:
    if not _NAME_RE.fullmatch(name or ""):
        raise ValueError("invalid name")
    return name


def validate_key_id(key_id: str) -> str:
    if not _KEY_ID_RE.fullmatch(key_id or ""):
        raise ValueError("invalid key id")
    return key_id


def bearer_token(header: str | None) -> str | None:
    """Return the bearer credential, or None. Never log ``header``."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not value or len(value) > 200 or any(c.isspace() for c in value):
        return None
    return value


@dataclass(frozen=True)
class IssuedKey:
    key_id: str
    name: str
    prefix: str
    token: str
    token_hash: str
    created_at: str

    def __repr__(self) -> str:
        return f"IssuedKey(key_id={self.key_id!r}, name={self.name!r}, prefix={self.prefix!r})"


@dataclass(frozen=True)
class StoredKey:
    key_id: str
    name: str
    prefix: str
    token_hash: str
    created_at: str
    revoked_at: str | None


def public_fields(issued: IssuedKey) -> dict[str, str]:
    """Fields that may be persisted. The plaintext token is not one of them."""
    fields = {
        "key_id": issued.key_id,
        "name": issued.name,
        "prefix": issued.prefix,
        "token_hash": issued.token_hash,
        "created_at": issued.created_at,
        "revoked_at": "",
    }
    if issued.token and issued.token in "".join(fields.values()):
        raise KeyStoreError("refusing to store plaintext")
    return fields


def new_key(name: str, now: str | None = None) -> IssuedKey:
    token = "cc_" + secrets.token_urlsafe(32)
    remember_plaintext(token)
    if not _TOKEN_RE.fullmatch(token):
        raise KeyStoreError("token shape")
    return IssuedKey(
        key_id="ck_" + secrets.token_hex(8),
        name=name,
        prefix=token[-4:],
        token=token,
        token_hash=hash_token(token),
        created_at=now or utc_now(),
    )


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, datetime):
        return value.isoformat()
    return "" if value is None else str(value)


def _run_store(fn: Callable[[], object]) -> object:
    try:
        return fn()
    except KeyStoreError:
        raise
    except Exception as exc:
        raise KeyStoreError(type(exc).__name__) from None


class RedisKeyStore:
    """Hash records at ``cc:api_key:{id}`` and ``cc:api_key_hash:{sha256}``."""

    def __init__(self, client: object):
        self._client = client

    def save(self, issued: IssuedKey) -> None:
        fields = public_fields(issued)

        def run() -> None:
            client = self._client
            client.hset(f"cc:api_key:{fields['key_id']}", mapping=fields)  # type: ignore[attr-defined]
            client.set(f"cc:api_key_hash:{fields['token_hash']}", fields["key_id"])  # type: ignore[attr-defined]

        _run_store(run)

    def revoke(self, key_id: str, revoked_at: str) -> bool:
        def run() -> bool:
            client = self._client
            key = f"cc:api_key:{key_id}"
            current = client.hgetall(key)  # type: ignore[attr-defined]
            if not current:
                return False
            client.hset(key, mapping={"revoked_at": revoked_at})  # type: ignore[attr-defined]
            return True

        return bool(_run_store(run))

    def lookup_hash(self, token_hash: str) -> StoredKey | None:
        def run() -> StoredKey | None:
            client = self._client
            key_id = client.get(f"cc:api_key_hash:{token_hash}")  # type: ignore[attr-defined]
            if not key_id:
                return None
            key_id = _text(key_id)
            data = client.hgetall(f"cc:api_key:{key_id}")  # type: ignore[attr-defined]
            if not data:
                return None
            fields = {_text(k): _text(v) for k, v in data.items()}
            if fields.get("token_hash") != token_hash:
                return None
            revoked = fields.get("revoked_at") or None
            return StoredKey(
                key_id=fields.get("key_id") or key_id,
                name=fields.get("name") or "",
                prefix=fields.get("prefix") or "",
                token_hash=token_hash,
                created_at=fields.get("created_at") or "",
                revoked_at=revoked,
            )

        result = _run_store(run)
        return result if isinstance(result, StoredKey) or result is None else None


class PostgresKeyStore:
    """Append-only rows. ``connect`` returns a DB-API connection."""

    def __init__(self, connect: Callable[[], object]):
        self._connect = connect

    def _run(self, fn: Callable[[object], object]) -> object:
        def run() -> object:
            conn = self._connect()
            try:
                cur = conn.cursor()
                try:
                    result = fn(cur)
                finally:
                    cur.close()
                conn.commit()
                return result
            except Exception:
                rollback = getattr(conn, "rollback", None)
                if rollback is not None:
                    rollback()
                raise
            finally:
                conn.close()

        return _run_store(run)

    def save(self, issued: IssuedKey) -> None:
        fields = public_fields(issued)

        def run(cur: object) -> None:
            cur.execute(  # type: ignore[attr-defined]
                INSERT_KEY_SQL,
                (fields["key_id"], fields["name"], fields["prefix"], fields["token_hash"], fields["created_at"]),
            )

        self._run(run)

    def revoke(self, key_id: str, revoked_at: str) -> bool:
        def run(cur: object) -> bool:
            cur.execute(SELECT_ID_SQL, (key_id,))  # type: ignore[attr-defined]
            if cur.fetchone() is None:  # type: ignore[attr-defined]
                return False
            cur.execute(INSERT_REVOCATION_SQL, (key_id, revoked_at))  # type: ignore[attr-defined]
            return True

        return bool(self._run(run))

    def lookup_hash(self, token_hash: str) -> StoredKey | None:
        def run(cur: object) -> StoredKey | None:
            cur.execute(LOOKUP_SQL, (token_hash,))  # type: ignore[attr-defined]
            row = cur.fetchone()  # type: ignore[attr-defined]
            if not row:
                return None
            key_id, name, prefix, stored_hash, created_at, revoked_at = row
            if _text(stored_hash) != token_hash:
                return None
            revoked = _text(revoked_at) if revoked_at else None
            return StoredKey(
                key_id=_text(key_id),
                name=_text(name),
                prefix=_text(prefix),
                token_hash=token_hash,
                created_at=_text(created_at),
                revoked_at=revoked,
            )

        result = self._run(run)
        return result if isinstance(result, StoredKey) or result is None else None


class ApiKeyDirectory:
    """Mint, revoke, and authenticate across one or more stores.

    ``both`` authenticates only when every store has the hash and none of them
    has a revocation. A failed mint revokes the stores that already saved it.
    """

    def __init__(self, stores: list[object]):
        if not stores:
            raise KeyStoreError("no key store")
        self._stores = stores

    def mint(self, name: str) -> IssuedKey:
        issued = new_key(validate_name(name))
        saved: list[object] = []
        try:
            for store in self._stores:
                store.save(issued)  # type: ignore[attr-defined]
                saved.append(store)
        except Exception as exc:
            revoked_at = utc_now()
            for store in saved:
                try:
                    store.revoke(issued.key_id, revoked_at)  # type: ignore[attr-defined]
                except Exception:
                    logger.warning("cc_api_key compensate failed key_id=%s", issued.key_id)
            if isinstance(exc, KeyStoreError):
                raise
            raise KeyStoreError(type(exc).__name__) from None
        logger.info("cc_api_key minted key_id=%s name=%s", issued.key_id, issued.name)
        return issued

    def revoke(self, key_id: str) -> None:
        key_id = validate_key_id(key_id)
        found = False
        first_error: KeyStoreError | None = None
        revoked_at = utc_now()
        for store in self._stores:
            try:
                if store.revoke(key_id, revoked_at):  # type: ignore[attr-defined]
                    found = True
            except KeyStoreError as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error
        if not found:
            raise KeyNotFound(key_id)
        logger.info("cc_api_key revoked key_id=%s", key_id)

    def authenticate(self, token: str) -> StoredKey | None:
        """Return the stored record, or None when the token is missing or revoked.

        Store outages raise ``KeyStoreError`` so the caller can answer 503
        instead of treating a blind store as a bad password.
        """
        if not token or not _TOKEN_RE.fullmatch(token):
            return None
        digest = hash_token(token)
        chosen: StoredKey | None = None
        for store in self._stores:
            row = store.lookup_hash(digest)  # type: ignore[attr-defined]
            if row is None or row.revoked_at:
                return None
            chosen = row
        return chosen


class _LazyClient:
    def __init__(self, factory: Callable[[], object]):
        self._factory = factory
        self._client: object | None = None

    def __getattr__(self, name: str) -> object:
        if self._client is None:
            self._client = self._factory()
        return getattr(self._client, name)


def redis_connection_kwargs(env: Mapping[str, str]) -> dict[str, object]:
    """Connection kwargs from the canonical Redis variables. Ignores ``REDIS_URL``."""
    try:
        port = int(env.get("REDIS_PORT") or 6379)
        db = int(env.get("REDIS_DB") or 0)
    except (TypeError, ValueError) as exc:
        raise KeyStoreError("invalid REDIS_PORT or REDIS_DB") from exc
    password = env.get("REDIS_PASSWORD") or None
    return {
        "host": env.get("REDIS_HOST") or "localhost",
        "port": port,
        "db": db,
        "password": password,
        "socket_timeout": 2,
        "socket_connect_timeout": 2,
        "decode_responses": True,
    }


def redis_client_from_env(env: Mapping[str, str]) -> object:
    try:
        import redis
    except ImportError:
        raise KeyStoreError("redis package not installed") from None
    return redis.Redis(**redis_connection_kwargs(env))


def postgres_connect_from_env(env: Mapping[str, str]) -> object:
    try:
        import psycopg2
    except ImportError:
        raise KeyStoreError("psycopg2 not installed") from None
    password = env.get("DB_PASSWORD")
    if not password:
        raise KeyStoreError("DB_PASSWORD is required")
    try:
        port = int(env.get("DB_PORT") or 5432)
    except (TypeError, ValueError) as exc:
        raise KeyStoreError("invalid DB_PORT") from exc
    return psycopg2.connect(
        host=env.get("DB_HOST") or "localhost",
        port=port,
        dbname=env.get("DB_NAME") or "scraping_pipeline",
        user=env.get("DB_USER") or "postgres",
        password=password,
        connect_timeout=2,
    )


def store_from_env(env: Mapping[str, str] | None = None) -> ApiKeyDirectory:
    env = os.environ if env is None else env
    backend = (env.get("CC_API_KEY_BACKEND") or "redis").strip().lower()
    if backend == "redis":
        stores: list[object] = [RedisKeyStore(_LazyClient(lambda: redis_client_from_env(env)))]
    elif backend == "postgres":
        stores = [PostgresKeyStore(lambda: postgres_connect_from_env(env))]
    elif backend == "both":
        stores = [
            RedisKeyStore(_LazyClient(lambda: redis_client_from_env(env))),
            PostgresKeyStore(lambda: postgres_connect_from_env(env)),
        ]
    else:
        raise KeyStoreError("invalid CC_API_KEY_BACKEND")
    return ApiKeyDirectory(stores)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cc_auth",
        description="Mint and revoke hashed Control Center API keys (#658)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    mint = sub.add_parser("mint", help="Print a new key once and store only its hash")
    mint.add_argument("--name", required=True, help="Operator label (letters, digits, . _ -)")
    revoke = sub.add_parser("revoke", help="Revoke a key by id (ck_...), not by the secret")
    revoke.add_argument("key_id")
    args = parser.parse_args(argv)
    try:
        directory = store_from_env()
    except KeyStoreError as exc:
        print(f"key store error: {exc}", file=sys.stderr)
        return 1
    if args.command == "mint":
        try:
            issued = directory.mint(args.name)
        except ValueError:
            print("invalid name", file=sys.stderr)
            return 2
        except KeyStoreError as exc:
            print(f"key store error: {exc}", file=sys.stderr)
            return 1
        sys.stdout.write(f"key_id={issued.key_id} name={issued.name}\n")
        sys.stdout.write(issued.token + "\n")
        return 0
    try:
        directory.revoke(args.key_id)
    except ValueError:
        print("revoke takes a key id (ck_...), not the API key", file=sys.stderr)
        return 2
    except KeyNotFound:
        print(f"unknown key id {args.key_id}", file=sys.stderr)
        return 1
    except KeyStoreError as exc:
        print(f"key store error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(f"revoked key_id={args.key_id}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
