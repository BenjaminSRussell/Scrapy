"""Read-only SQL over Delta tables (#635).

Off unless ``LAKE_SQL_ENABLED`` is set. One SELECT (or WITH … SELECT) per call.
DuckDB, when installed, sees each Delta table as a relation over its current
parquet files. A statement timeout and a row cap always apply. File readers,
multi-statements, and anything other than SELECT are rejected.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from src.utils.feature_flags import get_bool, get_int, get_str

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 5.0
DEFAULT_ROW_CAP = 1000
MIN_TIMEOUT_S = 0.1
MAX_TIMEOUT_S = 30.0
MIN_ROW_CAP = 1
MAX_ROW_CAP = 10_000
MAX_SQL_CHARS = 8_000

# Whole tokens only (quoted identifiers and string literals are not tokens).
# ``replace`` is omitted: it is a normal scalar function, not only a write.
_FORBIDDEN = frozenset(
    {
        "alter",
        "analyze",
        "analyse",
        "attach",
        "begin",
        "call",
        "checkpoint",
        "commit",
        "copy",
        "create",
        "deallocate",
        "delete",
        "delta_scan",
        "detach",
        "drop",
        "duckdb_secrets",
        "execute",
        "explain",
        "export",
        "export_database",
        "getenv",
        "glob",
        "grant",
        "http_get",
        "httpfs",
        "iceberg_scan",
        "import",
        "import_database",
        "insert",
        "install",
        "into",
        "load",
        "load_aws_credentials",
        "load_extension",
        "merge",
        "mysql_scan",
        "parquet_scan",
        "postgres_scan",
        "pragma",
        "prepare",
        "read_blob",
        "read_csv",
        "read_csv_auto",
        "read_json",
        "read_json_auto",
        "read_ndjson",
        "read_ndjson_objects",
        "read_parquet",
        "read_text",
        "read_xlsx",
        "reset",
        "revoke",
        "rollback",
        "set",
        "sqlite_scan",
        "truncate",
        "update",
        "use",
        "vacuum",
    }
)

_TABLE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_USER_ERRORS = frozenset(
    {
        "BinderException",
        "CatalogException",
        "ConversionException",
        "InvalidInputException",
        "ParserException",
    }
)
_FROM_WORDS = frozenset({"from", "join"})


class SqlRejected(ValueError):
    """The statement is not a single read-only SELECT."""


class LakeSqlDisabled(RuntimeError):
    """``LAKE_SQL_ENABLED`` is off."""


class LakeSqlUnavailable(RuntimeError):
    """DuckDB or the lake cannot answer this request."""


class QueryTimeout(TimeoutError):
    """The statement exceeded the timeout."""


class QueryFailed(RuntimeError):
    """DuckDB rejected the SELECT (binder, catalog, conversion)."""


def enabled(env: Mapping[str, str] | None = None) -> bool:
    """True only when the flag is explicitly on. Default is off."""
    return get_bool("LAKE_SQL_ENABLED", False, env=env)


def timeout_seconds(env: Mapping[str, str] | None = None) -> float:
    text = get_str("LAKE_SQL_TIMEOUT_SECONDS", "", env=env)
    if not text:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(text)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    if not math.isfinite(value):
        return DEFAULT_TIMEOUT_S
    return min(MAX_TIMEOUT_S, max(MIN_TIMEOUT_S, value))


def row_cap(env: Mapping[str, str] | None = None) -> int:
    value = get_int("LAKE_SQL_ROW_CAP", DEFAULT_ROW_CAP, env=env)
    return min(MAX_ROW_CAP, max(MIN_ROW_CAP, value))


def lake_base_path(
    base_path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Lake root. Does not create it.

    Explicit ``base_path`` wins, then ``DELTA_LAKE_PATH``, then config
    ``delta_lake.base_path`` when ``env`` is the process environment.
    """
    if base_path is not None:
        return Path(base_path)
    source = os.environ if env is None else env
    raw = str(source.get("DELTA_LAKE_PATH") or "").strip()
    if raw:
        return Path(raw)
    if env is not None:
        return Path("./data/delta_lake")
    try:
        from src.core.config import get_config

        configured = get_config().get("delta_lake.base_path", "./data/delta_lake")
    except Exception:
        configured = "./data/delta_lake"
    return Path(str(configured))


def validate_select(sql: str) -> str:
    """Return comment-free SQL for one SELECT, or raise ``SqlRejected``."""
    if not isinstance(sql, str):
        raise SqlRejected("sql must be a string")
    if "\x00" in sql:
        raise SqlRejected("query is not valid text")
    if len(sql) > MAX_SQL_CHARS:
        raise SqlRejected("query is too long")
    if not sql.strip():
        raise SqlRejected("empty query")

    tokens, cleaned = _scan(sql)
    semis = [i for i, tok in enumerate(tokens) if tok[0] == "semi"]
    if len(semis) > 1 or (len(semis) == 1 and semis[0] != len(tokens) - 1):
        raise SqlRejected("multiple statements are not allowed")

    depth = 0
    for kind, text in tokens:
        if kind != "paren":
            continue
        depth += 1 if text == "(" else -1
        if depth < 0:
            raise SqlRejected("unbalanced parentheses")
    if depth != 0:
        raise SqlRejected("unbalanced parentheses")

    words = [text for kind, text in tokens if kind == "word"]
    if not words or _head(words[0]) not in {"select", "with"}:
        raise SqlRejected("only SELECT statements are allowed")
    if _head(words[0]) == "with" and not any(_head(word) == "select" or "select" in _parts(word) for word in words):
        raise SqlRejected("only SELECT statements are allowed")

    for word in words:
        for part in _parts(word):
            if part in _FORBIDDEN:
                raise SqlRejected(f"SELECT-only: {part} is not allowed")
    _reject_quoted_calls(tokens)

    after_relation = False
    for kind, text in tokens:
        if kind == "word" and text.lower() in _FROM_WORDS:
            after_relation = True
            continue
        if after_relation and kind == "comma":
            continue
        if after_relation:
            if kind == "string":
                raise SqlRejected("SELECT-only: file paths are not allowed")
            after_relation = False

    statement = cleaned.rstrip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
    if not statement:
        raise SqlRejected("empty query")
    return statement


def status(env: Mapping[str, str] | None = None, base_path: str | Path | None = None) -> dict[str, Any]:
    """Endpoint description. Table names are listed only when the flag is on."""
    on = enabled(env)
    body: dict[str, Any] = {
        "ok": True,
        "enabled": on,
        "timeout_seconds": timeout_seconds(env),
        "row_cap": row_cap(env),
    }
    if on:
        base = lake_base_path(base_path, env)
        body["tables"] = [name for name, _path in _discover(base)]
    return body


def query_delta(
    sql: str,
    *,
    env: Mapping[str, str] | None = None,
    base_path: str | Path | None = None,
    enabled_flag: bool | None = None,
    timeout_s: float | None = None,
    row_cap_n: int | None = None,
    connect: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Run one SELECT. Raises when disabled, rejected, timed out, or unavailable."""
    if enabled_flag is None:
        enabled_flag = enabled(env)
    if not enabled_flag:
        raise LakeSqlDisabled("Lake SQL is disabled")
    statement = validate_select(sql)
    timeout = DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s
    limit = DEFAULT_ROW_CAP if row_cap_n is None else row_cap_n
    if timeout_s is None:
        timeout = timeout_seconds(env)
    if row_cap_n is None:
        limit = row_cap(env)
    timeout = min(MAX_TIMEOUT_S, max(MIN_TIMEOUT_S, float(timeout)))
    limit = min(MAX_ROW_CAP, max(MIN_ROW_CAP, int(limit)))
    base = lake_base_path(base_path, env)

    started = time.monotonic()
    con = _open_connection(connect)
    try:
        attached, skipped = _register_tables(con, base)
        _lock_down(con, base)
        try:
            rows, columns = _run_query(con, statement, timeout, limit)
        except QueryTimeout:
            raise
        except Exception as exc:
            if _is_user_query_error(exc):
                raise QueryFailed(_public_error(exc, base)) from exc
            raise LakeSqlUnavailable("query failed") from exc
    finally:
        _close(con)

    trimmed = rows[:limit]
    records = [_record(row, columns) for row in trimmed]
    return {
        "ok": True,
        "columns": columns,
        "rows": records,
        "row_count": len(records),
        "truncated": len(rows) > limit,
        "tables": attached,
        "skipped_tables": skipped,
        "row_cap": limit,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }


def run_http(
    sql: str,
    env: Mapping[str, str] | None = None,
    *,
    base_path: str | Path | None = None,
    connect: Callable[[], Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """HTTP status and JSON body for one SQL request."""
    if not enabled(env):
        return 403, {"ok": False, "error": "disabled"}
    try:
        payload = query_delta(sql, env=env, base_path=base_path, enabled_flag=True, connect=connect)
    except SqlRejected as exc:
        return 400, {"ok": False, "error": str(exc)}
    except QueryFailed as exc:
        return 400, {"ok": False, "error": str(exc)}
    except QueryTimeout:
        return 408, {"ok": False, "error": "timeout"}
    except LakeSqlUnavailable as exc:
        return 503, {"ok": False, "error": str(exc)}
    except Exception:
        logger.warning("lake sql query failed", exc_info=True)
        return 500, {"ok": False, "error": "query failed"}
    return 200, payload


def _reject_quoted_calls(tokens: list[tuple[str, str]]) -> None:
    """Quoted identifiers are allowed as column names, not as file-reader calls."""
    for index, (kind, text) in enumerate(tokens):
        if kind != "ident":
            continue
        name = text[1:-1].replace('""', '"').replace("``", "`").lower()
        if name not in _FORBIDDEN:
            continue
        nxt = next((tok for tok in tokens[index + 1 :] if tok[0] != "comma"), None)
        if nxt is not None and nxt[0] == "paren" and nxt[1] == "(":
            raise SqlRejected(f"SELECT-only: {name} is not allowed")


def _head(word: str) -> str:
    return _parts(word)[0] if _parts(word) else ""


def _parts(word: str) -> list[str]:
    return [part for part in word.lower().split(".") if part]


def _scan(sql: str) -> tuple[list[tuple[str, str]], str]:
    tokens: list[tuple[str, str]] = []
    cleaned: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if ch == "-" and nxt == "-":
            i += 2
            while i < n and sql[i] != "\n":
                i += 1
            cleaned.append(" ")
            continue
        if ch == "/" and nxt == "*":
            end = sql.find("*/", i + 2)
            if end < 0:
                raise SqlRejected("unterminated comment")
            i = end + 2
            cleaned.append(" ")
            continue
        if ch == "$" and nxt == "$":
            raise SqlRejected("dollar-quoting is not allowed")
        if ch in {"'", '"', "`"}:
            kind = "string" if ch == "'" else "ident"
            end = _quoted_end(sql, i, ch)
            tokens.append((kind, sql[i : end + 1]))
            cleaned.append(sql[i : end + 1])
            i = end + 1
            continue
        if ch == ";":
            tokens.append(("semi", ";"))
            cleaned.append(";")
            i += 1
            continue
        if ch in "()":
            tokens.append(("paren", ch))
            cleaned.append(ch)
            i += 1
            continue
        if ch == ",":
            tokens.append(("comma", ","))
            cleaned.append(",")
            i += 1
            continue
        if ch.isspace():
            cleaned.append(ch)
            i += 1
            continue
        j = i + 1
        while j < n:
            cj = sql[j]
            if cj.isspace() or cj in "();,":
                break
            if cj in {"'", '"', "`"}:
                break
            if cj == "-" and j + 1 < n and sql[j + 1] == "-":
                break
            if cj == "/" and j + 1 < n and sql[j + 1] == "*":
                break
            if cj == "$" and j + 1 < n and sql[j + 1] == "$":
                break
            j += 1
        tokens.append(("word", sql[i:j]))
        cleaned.append(sql[i:j])
        i = j
    return tokens, "".join(cleaned)


def _quoted_end(sql: str, start: int, quote: str) -> int:
    j = start + 1
    n = len(sql)
    while j < n:
        if sql[j] == quote:
            if j + 1 < n and sql[j + 1] == quote:
                j += 2
                continue
            return j
        j += 1
    label = "string" if quote == "'" else "identifier"
    raise SqlRejected(f"unterminated {label}")


def _open_connection(connect: Callable[[], Any] | None) -> Any:
    if connect is not None:
        return connect()
    try:
        import duckdb
    except ImportError as exc:
        raise LakeSqlUnavailable("duckdb is not installed") from exc
    return duckdb.connect(database=":memory:")


def _close(con: Any) -> None:
    close = getattr(con, "close", None)
    if not close:
        return
    try:
        close()
    except Exception:
        logger.debug("closing lake sql connection failed", exc_info=True)


def _discover(base: Path) -> list[tuple[str, Path]]:
    if not base.is_dir():
        return []
    root = base.resolve()
    # A lake root of "/" or "/tmp" would allow-list far more than the Delta tables.
    if len(root.parts) < 3:
        raise LakeSqlUnavailable("lake path is not usable")
    try:
        children = sorted(base.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise LakeSqlUnavailable("lake path is not readable") from exc
    found: list[tuple[str, Path]] = []
    for child in children:
        if not _TABLE_NAME.match(child.name):
            continue
        if not (child / "_delta_log").is_dir():
            continue
        try:
            resolved = child.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root):
            continue
        found.append((child.name, resolved))
    return found


def _delta_table_type() -> Any:
    try:
        from deltalake import DeltaTable
    except ImportError as exc:
        raise LakeSqlUnavailable("deltalake is not installed") from exc
    return DeltaTable


def _uri_to_path(uri: str) -> Path | None:
    """Local path for a Delta file URI. Remote schemes are ignored."""
    if "://" in uri:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return None
        return Path(unquote(parsed.path))
    return Path(uri)


def _files_under_table(uris: list[str], table_path: Path) -> list[Path]:
    root = table_path.resolve()
    files: list[Path] = []
    for uri in uris:
        raw = _uri_to_path(uri)
        if raw is None:
            continue
        candidates = [raw] if raw.is_absolute() else [raw, table_path / raw]
        for cand in candidates:
            try:
                resolved = cand.resolve()
            except OSError:
                continue
            if resolved.is_file() and resolved.is_relative_to(root):
                files.append(resolved)
                break
    return files


def _active_files(table_path: Path) -> list[Path]:
    delta_table = _delta_table_type()
    uris = [str(item) for item in delta_table(str(table_path)).file_uris()]
    return _files_under_table(uris, table_path)


def _attach_files(con: Any, name: str, files: list[Path]) -> None:
    reader = getattr(con, "read_parquet", None)
    if not callable(reader):
        raise LakeSqlUnavailable("duckdb cannot read parquet")
    relation = reader([str(path) for path in files], union_by_name=True)
    try:
        relation.create_view(name, replace=True)
    except TypeError:
        relation.create_view(name)


def _attach_empty(con: Any, name: str, table_path: Path) -> None:
    delta_table = _delta_table_type()
    arrow = delta_table(str(table_path)).to_pyarrow_table()
    empty = arrow.slice(0, 0)
    register = getattr(con, "register", None)
    if not callable(register):
        raise LakeSqlUnavailable("duckdb cannot register an empty table")
    register(name, empty)


def _register_tables(con: Any, base: Path) -> tuple[list[str], list[str]]:
    attached: list[str] = []
    skipped: list[str] = []
    for name, path in _discover(base):
        try:
            files = _active_files(path)
            if files:
                _attach_files(con, name, files)
            else:
                _attach_empty(con, name, path)
        except LakeSqlUnavailable:
            raise
        except Exception:
            logger.warning("skipping delta table %s for lake sql", name, exc_info=True)
            skipped.append(name)
            continue
        attached.append(name)
    return attached, skipped


def _sql_literal(value: str) -> str:
    if any(ch in value for ch in ("\x00", "\n", "\r")):
        raise LakeSqlUnavailable("lake path is not usable")
    return "'" + value.replace("'", "''") + "'"


def _lock_down(con: Any, base: Path) -> None:
    """Confine file access to the lake, then freeze settings.

    ``allowed_directories`` is an allow-list that still works after
    ``enable_external_access`` is turned off (DuckDB 1.1+). Older builds ignore
    unknown settings. The SELECT gate is what rejects non-SELECT text.
    """
    execute = getattr(con, "execute", None)
    if not callable(execute):
        return

    def _try(statement: str) -> bool:
        try:
            execute(statement)
            return True
        except Exception:
            logger.debug("lake sql session setting skipped", exc_info=True)
            return False

    _try("SET threads=1")
    _try("SET memory_limit='512MB'")
    _try("SET autoinstall_known_extensions=false")
    _try("SET autoload_known_extensions=false")
    allowed = False
    if base.exists():
        try:
            allowed = _try(f"SET allowed_directories=[{_sql_literal(str(base.resolve()))}]")
        except LakeSqlUnavailable:
            allowed = False
    # No lake: nothing to read. Lake with an allow-list: only that tree.
    # If the allow-list did not stick, leave external access on so registered
    # parquet relations still open; the SELECT gate still blocks readers.
    if allowed or not base.exists():
        _try("SET enable_external_access=false")
    _try("SET lock_configuration=true")


def _run_query(con: Any, statement: str, timeout_s: float, limit: int) -> tuple[list[Any], list[str]]:
    interrupted = threading.Event()

    def _poke() -> None:
        interrupted.set()
        interrupt = getattr(con, "interrupt", None)
        if not callable(interrupt):
            return
        try:
            interrupt()
        except Exception:
            logger.debug("duckdb interrupt failed", exc_info=True)

    timer = threading.Timer(timeout_s, _poke)
    timer.daemon = True
    timer.start()
    try:
        rows, columns = _fetch_limited(con, statement, limit)
    except Exception as exc:
        if interrupted.is_set():
            raise QueryTimeout("timeout") from exc
        raise
    finally:
        timer.cancel()
    return rows, columns


def _fetch_limited(con: Any, statement: str, limit: int) -> tuple[list[Any], list[str]]:
    fetch_n = limit + 1
    sql_fn = getattr(con, "sql", None)
    if callable(sql_fn):
        relation = sql_fn(statement)
        limiter = getattr(relation, "limit", None)
        if callable(limiter):
            relation = limiter(fetch_n)
        rows = _fetch(relation, fetch_n)
        return rows, _columns(relation, rows)
    wrapped = f"SELECT * FROM ({statement}) AS _lake_sql LIMIT {fetch_n}"
    cursor = con.execute(wrapped)
    rows = _fetch(cursor, fetch_n)
    return rows, _columns(cursor if getattr(cursor, "description", None) else con, rows)


def _fetch(source: Any, fetch_n: int) -> list[Any]:
    fetchmany = getattr(source, "fetchmany", None)
    if callable(fetchmany):
        return list(fetchmany(fetch_n))
    fetchall = getattr(source, "fetchall", None)
    if callable(fetchall):
        return list(fetchall())[:fetch_n]
    raise LakeSqlUnavailable("query returned no rows handle")


def _columns(source: Any, rows: list[Any]) -> list[str]:
    description = getattr(source, "description", None)
    if description:
        return [str(col[0]) for col in description]
    columns = getattr(source, "columns", None)
    if columns:
        return [str(col) for col in columns]
    if rows and isinstance(rows[0], Mapping):
        return [str(key) for key in rows[0]]
    if rows:
        return [str(index) for index in range(len(tuple(rows[0])))]
    return []


def _record(row: Any, columns: list[str]) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return {str(key): _jsonable(value) for key, value in row.items()}
    values = list(row)
    names = columns or [str(index) for index in range(len(values))]
    return {
        names[index] if index < len(names) else str(index): _jsonable(value)
        for index, value in enumerate(values)
    }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:
            pass
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes)):
        try:
            return _jsonable(item())
        except Exception:
            pass
    return str(value)


def _is_user_query_error(exc: BaseException) -> bool:
    return type(exc).__name__ in _USER_ERRORS


def _public_error(exc: BaseException, base: Path | None) -> str:
    text = " ".join(str(exc).split())
    if base is not None:
        for variant in (str(base.resolve()), str(base)):
            if variant:
                text = text.replace(variant, "<lake>")
    if len(text) > 300:
        text = text[:300] + "..."
    return text or type(exc).__name__
