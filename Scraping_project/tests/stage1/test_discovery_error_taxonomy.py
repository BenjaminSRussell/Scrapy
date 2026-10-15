"""Shared rustmapper / Stage 1 error codes and the retry table (#933)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.stage1.discovery_errors import (
    RETRY_POLICY_TABLE,
    DiscoveryErrorCode,
    DiscoveryRetryAction,
    classify_discovery_failure,
    coerce_error_code,
    select_handoff_code,
    stage1_retry_plan,
)
from src.stage1.discovery_handoff import (
    DiscoveryHandoff,
    DiscoveryStatus,
    consume_rustmapper_handoff,
)
from src.stage1.sitemap_parser import SitemapParser

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "discovery_errors"
DOC = Path(__file__).resolve().parents[3] / "docs" / "discovery_error_taxonomy.md"


class _Policy:
    queue_attempts = 3
    backoff_base = 0.5
    backoff_max = 8.0
    retryable_statuses = frozenset({500, 502, 503, 504, 408, 429})

    def in_request_statuses(self) -> frozenset[int]:
        return frozenset(code for code in self.retryable_statuses if code != 429)


def _fixture_payloads() -> list[dict]:
    payloads = []
    for path in sorted(FIXTURES.glob("*.json")):
        payloads.append(json.loads(path.read_text(encoding="utf-8")))
    return payloads


def test_fixture_for_every_code_and_table_row():
    payloads = _fixture_payloads()
    codes = {DiscoveryErrorCode(item["error_code"]) for item in payloads}
    assert codes == set(DiscoveryErrorCode)
    assert set(RETRY_POLICY_TABLE) == set(DiscoveryErrorCode)
    doc = DOC.read_text(encoding="utf-8")
    assert "Retry policy table" in doc
    for item in payloads:
        code = DiscoveryErrorCode(item["error_code"])
        action = DiscoveryRetryAction(item["retry_action"])
        assert RETRY_POLICY_TABLE[code] is action
        assert code.value in doc
        assert item["retry_action"] in doc
        handoff = DiscoveryHandoff.from_dict(item)
        assert handoff.error_code is code
        assert handoff.status.value == item["status"]
        plan = stage1_retry_plan(handoff.error_code, _Policy(), load=False)
        assert plan["action"] == action.value
        assert plan["code"] == code.value
        assert plan["retry"] is (action is DiscoveryRetryAction.QUEUE)


def test_queue_codes_reference_shared_retry_policy():
    policy = _Policy()
    timeout = stage1_retry_plan(DiscoveryErrorCode.TIMEOUT, policy, load=False)
    limited = stage1_retry_plan(DiscoveryErrorCode.HTTP_429, policy, load=False)
    assert timeout["queue_attempts"] == 3
    assert timeout["backoff_base"] == 0.5
    assert timeout["backoff_max"] == 8.0
    assert timeout["in_request"] is True
    assert "RetryPolicy" in timeout["policy_ref"]
    assert limited["queue_attempts"] == 3
    assert limited["in_request"] is False
    assert "in_request_statuses" in limited["policy_ref"]
    assert 429 in policy.retryable_statuses
    dropped = stage1_retry_plan(DiscoveryErrorCode.ROBOTS_DENY, policy, load=False)
    assert dropped["retry"] is False
    assert "queue_attempts" not in dropped


def test_classify_strings_and_leave_unknown_alone():
    assert classify_discovery_failure("sitemap fetch timed out") is DiscoveryErrorCode.TIMEOUT
    assert classify_discovery_failure("robots.txt disallowed /sitemap.xml") is DiscoveryErrorCode.ROBOTS_DENY
    assert classify_discovery_failure("HTTP 429 Too Many Requests") is DiscoveryErrorCode.HTTP_429
    assert classify_discovery_failure("failed to parse sitemap XML") is DiscoveryErrorCode.PARSE_FAIL
    assert classify_discovery_failure("robots.txt fetch timed out") is DiscoveryErrorCode.TIMEOUT
    assert classify_discovery_failure("boom") is None
    assert coerce_error_code("not-a-code") is None
    assert select_handoff_code(urls_found=False, codes=[]) is None


def test_timeout_exception_on_stage1_walk_is_queueable():
    parser = SitemapParser(
        "https://example.edu/",
        max_depth=1,
        max_urls=10,
        max_sitemaps=5,
        max_bytes=1024,
    )

    async def _boom() -> list[str]:
        raise httpx.TimeoutException("timed out")

    parser.discover_all_urls = _boom  # type: ignore[method-assign]
    handoff = asyncio.run(parser.discover_with_handoff())
    assert handoff.status == DiscoveryStatus.FAILED
    assert handoff.error_code is DiscoveryErrorCode.TIMEOUT
    assert stage1_retry_plan(handoff.error_code, _Policy(), load=False)["action"] == "queue"


class _Response:
    def __init__(self, status: int):
        self.status_code = status
        self.headers: dict[str, str] = {}


class _Stream:
    def __init__(self, status: int):
        self._response = _Response(status)

    async def __aenter__(self) -> _Response:
        return self._response

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Client:
    def __init__(self, status: int):
        self._status = status

    def stream(self, _method: str, _url: str) -> _Stream:
        return _Stream(self._status)


def _parser() -> SitemapParser:
    return SitemapParser(
        "https://example.edu/",
        max_depth=1,
        max_urls=10,
        max_sitemaps=5,
        max_bytes=1024,
    )


def test_http_429_is_coded_and_404_is_not():
    denied = _parser()
    missing = _parser()
    asyncio.run(denied._parse_sitemap_recursive(_Client(429), "https://example.edu/sitemap.xml", 0))
    asyncio.run(missing._parse_sitemap_recursive(_Client(404), "https://example.edu/sitemap.xml", 0))
    assert denied._error_codes == [DiscoveryErrorCode.HTTP_429]
    assert missing._error_codes == []
    assert missing._hard_errors == []
    code = select_handoff_code(urls_found=False, codes=denied._error_codes)
    assert code is DiscoveryErrorCode.HTTP_429
    assert select_handoff_code(urls_found=False, codes=missing._error_codes) is None


def test_rustmapper_jsonl_codes(tmp_path: Path):
    timed_out = tmp_path / "timeout.jsonl"
    timed_out.write_text(
        json.dumps({"error_code": "TIMEOUT", "error": "fetch timed out"}) + "\n",
        encoding="utf-8",
    )
    handoff = consume_rustmapper_handoff(timed_out, site="example.edu")
    assert handoff.status == DiscoveryStatus.FAILED
    assert handoff.error_code is DiscoveryErrorCode.TIMEOUT

    mixed = tmp_path / "partial.jsonl"
    mixed.write_text(
        "\n".join(
            [
                json.dumps({"url": "https://example.edu/a", "error_code": "OK"}),
                json.dumps({"error_code": "PARSE_FAIL", "error": "not well-formed"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    partial = consume_rustmapper_handoff(mixed, site="example.edu")
    assert partial.status == DiscoveryStatus.SUCCESS
    assert partial.error_code is DiscoveryErrorCode.PARTIAL
    assert partial.discovered == ["https://example.edu/a"]
    assert stage1_retry_plan(partial.error_code, load=False)["action"] == "ingest_partial"
