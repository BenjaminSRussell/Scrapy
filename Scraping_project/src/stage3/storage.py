"""Storage backends for Stage 3 enrichment outputs."""
from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class RotationPolicy:
    """Rotation policy for storage writers."""

    max_bytes: Optional[int] = None
    max_items: Optional[int] = None
    max_seconds: Optional[int] = None
    enabled: Optional[bool] = None

    def __post_init__(self) -> None:
        self.enabled = bool(self.enabled) if self.enabled is not None else any(
            value is not None for value in (self.max_bytes, self.max_items, self.max_seconds)
        )
        self._bytes_since_rotation = 0
        self._items_since_rotation = 0
        self._opened_at = time.time()

    def record(self, bytes_written: int, items_written: int = 1) -> None:
        """Record bytes/items for rotation tracking."""
        self._bytes_since_rotation += max(bytes_written, 0)
        self._items_since_rotation += max(items_written, 0)

    def should_rotate(self) -> bool:
        """Return True when any rotation limit is exceeded."""
        if not self.enabled:
            return False

        now = time.time()
        if self.max_bytes is not None and self._bytes_since_rotation >= self.max_bytes:
            return True
        if self.max_items is not None and self._items_since_rotation >= self.max_items:
            return True
        if self.max_seconds is not None and (now - self._opened_at) >= self.max_seconds:
            return True
        return False

    def reset(self) -> None:
        """Reset counters after a rotation occurs."""
        self._bytes_since_rotation = 0
        self._items_since_rotation = 0
        self._opened_at = time.time()

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "RotationPolicy":
        if not config:
            return cls()
        return cls(
            max_bytes=config.get("max_bytes"),
            max_items=config.get("max_items"),
            max_seconds=config.get("max_seconds"),
            enabled=config.get("enabled"),
        )


@dataclass
class CompressionConfig:
    """Compression configuration shared by storage writers."""

    codec: str = "none"
    level: Optional[int] = None
    use_extension: bool = True

    def __post_init__(self) -> None:
        self.codec = (self.codec or "none").lower()
        if self.codec not in {"none", "gzip", "snappy", "brotli", "zstd"}:
            raise ValueError(f"Unsupported compression codec: {self.codec}")
        if self.codec != "none" and self.level is not None:
            if not isinstance(self.level, int) or not (1 <= self.level <= 9):
                raise ValueError("Compression level must be between 1 and 9")

    @property
    def enabled(self) -> bool:
        return self.codec != "none"

    def extension(self) -> str:
        if not self.use_extension:
            return ""
        if self.codec == "gzip":
            return ".gz"
        return ""

    def parquet_codec(self) -> Optional[str]:
        if self.codec in {"snappy", "gzip", "brotli", "zstd"}:
            return self.codec
        if self.codec == "none":
            return None
        return None

    @classmethod
    def from_config(cls, config: Optional[Dict[str, Any]]) -> "CompressionConfig":
        if not config:
            return cls()
        return cls(
            codec=config.get("codec", "none"),
            level=config.get("level"),
            use_extension=config.get("use_extension", True),
        )


class BaseStorageWriter(ABC):
    """Abstract base class for enrichment storage writers."""

    def __init__(self, rotation: RotationPolicy, compression: CompressionConfig) -> None:
        self.rotation = rotation
        self.compression = compression

    @abstractmethod
    def open(self) -> None:
        """Open underlying resources."""

    @abstractmethod
    def write_item(self, item: Dict[str, Any]) -> None:
        """Persist a single enrichment item."""

    @abstractmethod
    def close(self) -> None:
        """Close resources and flush buffers."""

    @abstractmethod
    def describe_destination(self) -> str:
        """Human readable destination description."""


class JSONLStorageWriter(BaseStorageWriter):
    """Write enrichment items to local JSONL files with optional rotation."""

    def __init__(
        self,
        path: Path,
        rotation: RotationPolicy,
        compression: CompressionConfig,
        ensure_ascii: bool = False,
        flush_on_write: bool = True,
    ) -> None:
        super().__init__(rotation, compression)
        if compression.codec not in {"none", "gzip"}:
            raise ValueError("JSONL backend only supports 'none' or 'gzip' compression")
        self.base_path = Path(path)
        self.ensure_ascii = ensure_ascii
        self.flush_on_write = flush_on_write
        self._file_handle = None
        self._sequence = 0
        self._current_path: Optional[Path] = None

    def open(self) -> None:
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        self._sequence = 0
        self._open_new_file()

    def _resolve_path(self, sequence: int) -> Path:
        suffix = "".join(self.base_path.suffixes)
        stem = self.base_path.name[: -len(suffix)] if suffix else self.base_path.name
        if sequence == 0:
            candidate = self.base_path
        else:
            timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            candidate = self.base_path.with_name(f"{stem}{'-' if stem else ''}{timestamp}-{sequence:04d}{suffix}")
        extension = self.compression.extension()
        if extension and not "".join(candidate.suffixes).endswith(extension):
            candidate = candidate.with_name(candidate.name + extension)
        return candidate

    def _open_new_file(self) -> None:
        if self._file_handle:
            self._file_handle.close()
        path = self._resolve_path(self._sequence)
        mode = "at" if self.compression.codec == "gzip" else "a"
        if self.compression.codec == "gzip":
            compresslevel = self.compression.level or 5
            self._file_handle = gzip.open(path, mode, encoding="utf-8", compresslevel=compresslevel)
        else:
            self._file_handle = open(path, mode, encoding="utf-8")
        self._current_path = path
        self.rotation.reset()

    def write_item(self, item: Dict[str, Any]) -> None:
        if not self._file_handle:
            raise RuntimeError("JSONLStorageWriter is not open")
        line = json.dumps(item, ensure_ascii=self.ensure_ascii)
        payload = f"{line}\n"
        self._file_handle.write(payload)
        if self.flush_on_write:
            self._file_handle.flush()
        self.rotation.record(len(payload.encode("utf-8")))
        if self.rotation.should_rotate():
            self._sequence += 1
            self._open_new_file()

    def close(self) -> None:
        if self._file_handle:
            self._file_handle.flush()
            self._file_handle.close()
            self._file_handle = None

    def describe_destination(self) -> str:
        return str(self.base_path)


class SQLiteStorageWriter(BaseStorageWriter):
    """Persist enrichment items into a SQLite database."""

    def __init__(
        self,
        path: Path,
        rotation: RotationPolicy,
        compression: CompressionConfig,
        table_name: str = "enrichment_items",
        synchronous: str = "NORMAL",
        journal_mode: str = "WAL",
    ) -> None:
        if compression.codec not in {"none", "gzip"}:
            raise ValueError("SQLite backend only supports 'none' or 'gzip' compression")
        super().__init__(rotation, compression)
        self.base_path = Path(path)
        self.table_name = table_name
        self.synchronous = synchronous
        self.journal_mode = journal_mode
        self._connection: Optional[sqlite3.Connection] = None
        self._cursor: Optional[sqlite3.Cursor] = None
        self._sequence = 0
        self._current_path: Optional[Path] = None

    def open(self) -> None:
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        self._sequence = 0
        self._open_database()

    def _resolve_path(self, sequence: int) -> Path:
        suffix = "".join(self.base_path.suffixes)
        stem = self.base_path.name[: -len(suffix)] if suffix else self.base_path.name
        if sequence == 0:
            candidate = self.base_path
        else:
            timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            candidate = self.base_path.with_name(f"{stem}{'-' if stem else ''}{timestamp}-{sequence:04d}{suffix}")
        return candidate

    def _open_database(self) -> None:
        if self._connection:
            self._connection.commit()
            self._connection.close()
            if self.compression.codec == "gzip" and self._current_path:
                self._compress_sqlite_file(self._current_path)
        path = self._resolve_path(self._sequence)
        self._current_path = path
        self._connection = sqlite3.connect(path)
        self._cursor = self._connection.cursor()
        self._cursor.execute(f"PRAGMA synchronous={self.synchronous}")
        self._cursor.execute(f"PRAGMA journal_mode={self.journal_mode}")
        self._cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT,
                url_hash TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        self._cursor.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{self.table_name}_url_hash ON {self.table_name}(url_hash)"
        )
        self._connection.commit()
        self.rotation.reset()

    def write_item(self, item: Dict[str, Any]) -> None:
        if not self._connection:
            raise RuntimeError("SQLiteStorageWriter is not open")
        payload = json.dumps(item, ensure_ascii=False)
        created_at = datetime.utcnow().isoformat()
        self._cursor.execute(
            f"INSERT INTO {self.table_name} (url, url_hash, payload, created_at) VALUES (?, ?, ?, ?)",
            (item.get("url"), item.get("url_hash"), payload, created_at),
        )
        self._connection.commit()
        self.rotation.record(len(payload.encode("utf-8")))
        if self.rotation.should_rotate():
            self._sequence += 1
            self._open_database()

    def close(self) -> None:
        if self._cursor:
            self._cursor.close()
            self._cursor = None
        if self._connection:
            self._connection.commit()
            self._connection.close()
            self._connection = None
        if self.compression.codec == "gzip" and self._current_path:
            self._compress_sqlite_file(self._current_path)

    def _compress_sqlite_file(self, path: Path) -> None:
        extension = self.compression.extension()
        if not extension:
            return
        compressed_path = path.with_name(path.name + extension)
        compresslevel = self.compression.level or 5
        with open(path, "rb") as src, gzip.open(compressed_path, "wb", compresslevel=compresslevel) as dst:
            shutil.copyfileobj(src, dst)
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    def describe_destination(self) -> str:
        return str(self.base_path)


class ParquetStorageWriter(BaseStorageWriter):
    """Write enrichment items to Parquet files."""

    def __init__(
        self,
        path: Path,
        rotation: RotationPolicy,
        compression: CompressionConfig,
        batch_size: int = 500,
    ) -> None:
        if compression.codec not in {"none", "snappy", "gzip", "brotli", "zstd"}:
            raise ValueError("Parquet backend supports compression: none, snappy, gzip, brotli, zstd")
        super().__init__(rotation, compression)
        self.base_path = Path(path)
        self.batch_size = batch_size
        self._sequence = 0
        self._buffer: list[Dict[str, Any]] = []
        self._writer = None
        self._current_path: Optional[Path] = None
        self._pa = None
        self._pq = None

    def open(self) -> None:
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("Parquet backend requires 'pyarrow' to be installed") from exc
        self._pa = pa
        self._pq = pq
        self._sequence = 0
        self._open_writer()

    def _resolve_path(self, sequence: int) -> Path:
        suffix = "".join(self.base_path.suffixes) or ".parquet"
        stem = self.base_path.name[: -len(suffix)] if suffix else self.base_path.name
        if sequence == 0:
            candidate = self.base_path if self.base_path.suffixes else self.base_path.with_suffix(suffix)
        else:
            timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            candidate = self.base_path.with_name(f"{stem}{'-' if stem else ''}{timestamp}-{sequence:04d}{suffix}")
        extension = self.compression.extension()
        if extension and not "".join(candidate.suffixes).endswith(extension):
            candidate = candidate.with_name(candidate.name + extension)
        return candidate

    def _open_writer(self) -> None:
        if self._writer:
            self._writer.close()
        path = self._resolve_path(self._sequence)
        self._current_path = path
        self._writer = None
        self.rotation.reset()

    def _ensure_writer(self, table) -> None:
        if self._writer is None:
            compression = self.compression.parquet_codec()
            self._writer = self._pq.ParquetWriter(str(self._current_path), table.schema, compression=compression)

    def write_item(self, item: Dict[str, Any]) -> None:
        if not self._pa or not self._pq:
            raise RuntimeError("ParquetStorageWriter is not open")
        self._buffer.append(item)
        payload = json.dumps(item, ensure_ascii=False).encode("utf-8")
        self.rotation.record(len(payload))
        if len(self._buffer) >= self.batch_size:
            self._flush_buffer()
        if self.rotation.should_rotate():
            self._flush_buffer()
            self._sequence += 1
            self._open_writer()

    def _flush_buffer(self) -> None:
        if not self._buffer:
            return
        table = self._pa.Table.from_pylist(self._buffer)
        self._ensure_writer(table)
        self._writer.write_table(table)
        self._buffer.clear()

    def close(self) -> None:
        self._flush_buffer()
        if self._writer:
            self._writer.close()
            self._writer = None

    def describe_destination(self) -> str:
        return str(self.base_path)


class S3StorageWriter(BaseStorageWriter):
    """Upload enrichment payloads to Amazon S3 as JSONL objects."""

    def __init__(
        self,
        bucket: str,
        rotation: RotationPolicy,
        compression: CompressionConfig,
        prefix: str = "stage3/",
        base_name: Optional[str] = None,
        content_type: str = "application/json",
        acl: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        region_name: Optional[str] = None,
        profile_name: Optional[str] = None,
        extra_args: Optional[Dict[str, Any]] = None,
    ) -> None:
        if compression.codec not in {"none", "gzip"}:
            raise ValueError("S3 backend only supports 'none' or 'gzip' compression")
        super().__init__(rotation, compression)
        self.bucket = bucket
        self.prefix = prefix or ""
        self.base_name = base_name or "enriched"
        self.content_type = content_type
        self.acl = acl
        self.endpoint_url = endpoint_url
        self.region_name = region_name
        self.profile_name = profile_name
        self.extra_args = extra_args or {}
        self._buffer = StringIO()
        self._sequence = 0
        self._client = None

    def open(self) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("S3 backend requires 'boto3' to be installed") from exc
        session_kwargs = {}
        if self.profile_name:
            session_kwargs["profile_name"] = self.profile_name
        session = boto3.Session(**session_kwargs)
        self._client = session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region_name,
        )
        self._buffer = StringIO()
        self._sequence = 0
        self.rotation.reset()

    def _object_key(self) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        extension = ".jsonl"
        suffix = self.compression.extension()
        if suffix:
            extension = f"{extension}{suffix}"
        return f"{self.prefix}{self.base_name}-{timestamp}-{self._sequence:04d}{extension}"

    def _flush_to_s3(self) -> None:
        if not self._buffer.getvalue():
            return
        if not self._client:
            raise RuntimeError("S3StorageWriter is not open")
        key = self._object_key()
        body = self._buffer.getvalue().encode("utf-8")
        if self.compression.codec == "gzip":
            compresslevel = self.compression.level or 5
            compressed = BytesIO()
            with gzip.GzipFile(fileobj=compressed, mode="wb", compresslevel=compresslevel) as gz:
                gz.write(body)
            body = compressed.getvalue()
        put_kwargs = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": body,
            "ContentType": self.content_type,
        }
        if self.acl:
            put_kwargs["ACL"] = self.acl
        put_kwargs.update(self.extra_args)
        self._client.put_object(**put_kwargs)
        self._buffer = StringIO()
        self.rotation.reset()
        self._sequence += 1

    def write_item(self, item: Dict[str, Any]) -> None:
        line = json.dumps(item, ensure_ascii=False)
        payload = f"{line}\n"
        self._buffer.write(payload)
        self.rotation.record(len(payload.encode("utf-8")))
        if self.rotation.should_rotate():
            self._flush_to_s3()

    def close(self) -> None:
        if self._buffer.getvalue():
            self._flush_to_s3()
        self._client = None

    def describe_destination(self) -> str:
        return f"s3://{self.bucket}/{self.prefix}" if self.prefix else f"s3://{self.bucket}"


def _default_path_for_backend(default_path: Path, backend: str) -> Path:
    if backend == "jsonl":
        return default_path
    if backend == "sqlite":
        if not default_path.suffix:
            return default_path.with_suffix(".db")
        return default_path
    if backend == "parquet":
        if not default_path.suffix:
            return default_path.with_suffix(".parquet")
        return default_path
    return default_path


def create_storage_writer(
    backend: str,
    options: Dict[str, Any],
    rotation_config: Optional[Dict[str, Any]],
    compression_config: Optional[Dict[str, Any]],
    default_path: Path,
) -> BaseStorageWriter:
    """Factory for storage writers based on configuration."""
    backend = (backend or "jsonl").lower()
    rotation = RotationPolicy.from_config(rotation_config)
    compression = CompressionConfig.from_config(compression_config)

    if backend == "jsonl":
        path = Path(options.get("path") or default_path)
        path = _default_path_for_backend(path, backend)
        ensure_ascii = bool(options.get("ensure_ascii", False))
        flush_on_write = bool(options.get("flush_on_write", True))
        return JSONLStorageWriter(
            path=path,
            rotation=rotation,
            compression=compression,
            ensure_ascii=ensure_ascii,
            flush_on_write=flush_on_write,
        )

    if backend == "sqlite":
        path = Path(options.get("path") or default_path)
        path = _default_path_for_backend(path, backend)
        table_name = options.get("table_name", "enrichment_items")
        synchronous = options.get("synchronous", "NORMAL")
        journal_mode = options.get("journal_mode", "WAL")
        return SQLiteStorageWriter(
            path=path,
            rotation=rotation,
            compression=compression,
            table_name=table_name,
            synchronous=synchronous,
            journal_mode=journal_mode,
        )

    if backend == "parquet":
        path = Path(options.get("path") or default_path)
        path = _default_path_for_backend(path, backend)
        batch_size = int(options.get("batch_size", 500))
        return ParquetStorageWriter(
            path=path,
            rotation=rotation,
            compression=compression,
            batch_size=batch_size,
        )

    if backend == "s3":
        bucket = options.get("bucket")
        if not bucket:
            raise ValueError("S3 backend requires a 'bucket' option")
        prefix = options.get("prefix", "stage3/")
        base_name = options.get("base_name")
        content_type = options.get("content_type", "application/json")
        acl = options.get("acl")
        endpoint_url = options.get("endpoint_url")
        region_name = options.get("region_name")
        profile_name = options.get("profile_name")
        extra_args = options.get("extra_args", {})
        return S3StorageWriter(
            bucket=bucket,
            rotation=rotation,
            compression=compression,
            prefix=prefix,
            base_name=base_name or default_path.stem or "enriched",
            content_type=content_type,
            acl=acl,
            endpoint_url=endpoint_url,
            region_name=region_name,
            profile_name=profile_name,
            extra_args=extra_args,
        )

    raise ValueError(f"Unsupported storage backend: {backend}")
