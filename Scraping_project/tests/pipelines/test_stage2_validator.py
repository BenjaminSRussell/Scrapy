"""Tests for Stage 2 URL Validator."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from src.stage2.validator import URLValidator
from tests.samples import build_validation_result


class DummyConfig:
    """Minimal config shim replicating the orchestrator Config behaviour."""

    def __init__(self, output_file: Path):
        self.output_file = output_file

    def get_stage2_config(self):
        return {
            "max_workers": 4,
            "timeout": 2,
            "output_file": str(self.output_file),
        }

    def get(self, *keys: str, default=None):
        if keys == ("scrapy", "user_agent"):
            return "TestValidator/1.0"
        return default


class StubResponse:
    """Async context manager mimicking aiohttp response objects."""

    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        url: str = "https://uconn.edu/test",
    ):
        self.status = status
        self.headers = headers or {}
        self._body = body or b"<html></html>"
        self.url = url

    async def __aenter__(self) -> StubResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def read(self) -> bytes:
        return self._body


class StubSession:
    """Async context manager replacing aiohttp.ClientSession for tests."""

    def __init__(
        self,
        *,
        head_response: StubResponse | Exception,
        get_response: StubResponse | Exception | None = None,
    ):
        self.head_response = head_response
        self.get_response = get_response or head_response
        self.head_calls: list[str] = []
        self.get_calls: list[str] = []

    async def __aenter__(self) -> StubSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def head(self, url: str, allow_redirects: bool = True):
        self.head_calls.append(url)
        if isinstance(self.head_response, Exception):
            raise self.head_response
        return self.head_response

    def get(self, url: str, allow_redirects: bool = True):
        self.get_calls.append(url)
        if isinstance(self.get_response, Exception):
            raise self.get_response
        return self.get_response


def test_url_validator_initialization(tmp_path):
    output = tmp_path / "stage02" / "validated.jsonl"
    validator = URLValidator(DummyConfig(output))

    assert validator.output_file == output
    assert validator.max_workers == 4
    assert validator.timeout == 2
    assert validator.connector_limit == 8  # max_workers * 2 capped at 100


def test_validate_single_url():
    response_headers = {"Content-Type": "text/html", "Content-Length": "512"}
    session = StubSession(
        head_response=StubResponse(headers=response_headers, status=200, url="https://uconn.edu/page"),
    )

    validator = URLValidator(DummyConfig(Path("/tmp/validated.jsonl")))
    result = asyncio.run(validator.validate_url(session, "https://uconn.edu/page", "hash1"))

    assert result.url == "https://uconn.edu/page"
    assert result.status_code == 200
    assert result.is_valid is True
    assert session.head_calls == ["https://uconn.edu/page"]
    assert session.get_calls == []


def test_head_fallback_to_get():
    head_headers = {"Content-Type": "", "Content-Length": "0"}
    get_headers = {"Content-Type": "text/html", "Content-Length": ""}
    head_response = StubResponse(headers=head_headers, status=200)
    get_response = StubResponse(headers=get_headers, status=200, body=b"<html>content</html>")

    session = StubSession(head_response=head_response, get_response=get_response)

    validator = URLValidator(DummyConfig(Path("/tmp/validated.jsonl")))
    result = asyncio.run(validator.validate_url(session, "https://uconn.edu/page", "hash2"))

    assert result.content_length == len(b"<html>content</html>")
    assert session.get_calls == ["https://uconn.edu/page"]


def test_validation_timeout_handling():
    timeout_error = TimeoutError()
    session = StubSession(head_response=timeout_error)

    validator = URLValidator(DummyConfig(Path("/tmp/validated.jsonl")))
    result = asyncio.run(validator.validate_url(session, "https://uconn.edu/slow", "hash3"))

    assert result.is_valid is False
    assert result.status_code == 0
    assert result.error_message == "Request timeout"


def test_validate_batch_writes_results(tmp_path, monkeypatch):
    output = tmp_path / "validated.jsonl"
    config = DummyConfig(output)
    validator = URLValidator(config)

    batch_items = [
        SimpleNamespace(url=f"https://uconn.edu/page{i}", url_hash=f"hash_batch_{i}", data={})
        for i in range(3)
    ]

    async def fake_validate_url(session, url, url_hash):
        return build_validation_result(url=url, url_hash=url_hash)

    monkeypatch.setattr(validator, "validate_url", fake_validate_url)

    class DummyConnector:
        def __init__(self, *args, **kwargs):
            pass

    class DummyClientSession:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr("src.stage2.validator.aiohttp.ClientSession", DummyClientSession)
    monkeypatch.setattr("src.stage2.validator.aiohttp.TCPConnector", DummyConnector)

    asyncio.run(validator.validate_batch(batch_items))

    assert output.exists()
    expected = [asdict(build_validation_result(url=item.url, url_hash=item.url_hash)) for item in batch_items]
    written = _read_jsonl(output)
    assert len(written) == len(expected)
    for line, exp in zip(written, expected, strict=False):
        assert line["url_hash"] == exp["url_hash"]


def _read_jsonl(path: Path) -> list[dict[str, any]]:
    content: list[dict[str, any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            content.append(json.loads(line))
    return content
