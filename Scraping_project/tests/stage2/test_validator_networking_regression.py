"""
Comprehensive regression tests for Stage 2 URL validator networking behavior.

Tests the validator's HEAD/GET/error handling which is central to Stage 2 correctness.
Realistic aiohttp-style tests prevent silent regressions in network behavior.
"""

import pytest
import asyncio
import aiohttp
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime
from typing import List, Dict, Any

from stage2.validator import URLValidator
from orchestrator.pipeline import BatchQueueItem
from common.schemas import ValidationResult


class MockResponse:
    """Mock aiohttp response for testing"""

    def __init__(self, status: int, headers: Dict[str, str] = None, content: bytes = b"", url: str = ""):
        self.status = status
        self.headers = headers or {}
        self.url = Mock()
        self.url.__str__ = Mock(return_value=url)
        self._content = content

    async def read(self):
        return self._content

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class MockSession:
    """Mock aiohttp session for testing"""

    def __init__(self, responses: List[MockResponse]):
        self.responses = responses
        self.call_count = 0
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        else:
            # Default response for unexpected calls
            return MockResponse(404, content=b"Not Found")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.fixture
def mock_config():
    """Mock configuration for validator tests"""
    config = Mock()
    config.get_stage2_config.return_value = {
        'max_workers': 10,
        'timeout': 30,
        'output_file': 'test_validation_output.jsonl'
    }
    config.get.return_value = 'UConn-Spider/1.0'
    return config


@pytest.fixture
def temp_output_file():
    """Create temporary output file for testing"""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl') as f:
        temp_path = Path(f.name)
    yield temp_path
    if temp_path.exists():
        temp_path.unlink()


class TestURLValidatorNetworkingRegression:
    """Regression tests for URL validator networking behavior"""

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_successful_validation_complete_flow(self, mock_config):
        """Test complete successful validation flow with realistic responses"""
        validator = URLValidator(mock_config)

        # Mock successful HTML response
        html_content = b"""
        <!DOCTYPE html>
        <html>
        <head><title>UConn - University of Connecticut</title></head>
        <body><h1>Welcome to UConn</h1></body>
        </html>
        """

        mock_response = MockResponse(
            status=200,
            headers={
                'Content-Type': 'text/html; charset=utf-8',
                'Content-Length': str(len(html_content))
            },
            content=html_content,
            url="https://uconn.edu"
        )

        mock_session = MockSession([mock_response])

        result = await validator.validate_url(
            session=mock_session,
            url="https://uconn.edu",
            url_hash="test_hash_123"
        )

        # Verify successful validation
        assert result.url == "https://uconn.edu"
        assert result.url_hash == "test_hash_123"
        assert result.status_code == 200
        assert "text/html" in result.content_type
        assert result.content_length == len(html_content)
        assert result.is_valid is True
        assert result.error_message is None
        assert result.response_time > 0

        # Verify session was called correctly
        assert len(mock_session.requests) == 1
        assert mock_session.requests[0][0] == "GET"
        assert mock_session.requests[0][1] == "https://uconn.edu"

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_http_error_status_codes(self, mock_config):
        """Test handling of various HTTP error status codes"""
        validator = URLValidator(mock_config)

        error_test_cases = [
            (404, "Not Found"),
            (403, "Forbidden"),
            (500, "Internal Server Error"),
            (502, "Bad Gateway"),
            (503, "Service Unavailable"),
            (301, "Moved Permanently"),  # Redirect should be followed by aiohttp
        ]

        for status_code, status_text in error_test_cases:
            mock_response = MockResponse(
                status=status_code,
                headers={'Content-Type': 'text/html'},
                content=status_text.encode(),
                url="https://uconn.edu/error"
            )

            mock_session = MockSession([mock_response])

            result = await validator.validate_url(
                session=mock_session,
                url="https://uconn.edu/error",
                url_hash=f"error_hash_{status_code}"
            )

            # Verify error handling
            assert result.status_code == status_code
            assert result.url_hash == f"error_hash_{status_code}"

            # Status codes outside 200-399 should be invalid
            if not (200 <= status_code < 400):
                assert result.is_valid is False
                assert result.error_message == "Invalid response"
            else:
                # Check if HTML content type affects validity
                if 'text/html' in result.content_type:
                    assert result.is_valid is True

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_non_html_content_type_rejection(self, mock_config):
        """Test that non-HTML content types are properly rejected"""
        validator = URLValidator(mock_config)

        non_html_test_cases = [
            ("application/json", b'{"message": "API response"}'),
            ("text/plain", b"Plain text content"),
            ("application/pdf", b"%PDF-1.4 fake pdf content"),
            ("image/jpeg", b"\xff\xd8\xff\xe0 fake jpeg"),
            ("text/css", b"body { margin: 0; }"),
            ("application/javascript", b"console.log('test');"),
        ]

        for content_type, content in non_html_test_cases:
            mock_response = MockResponse(
                status=200,
                headers={'Content-Type': content_type},
                content=content,
                url="https://uconn.edu/resource"
            )

            mock_session = MockSession([mock_response])

            result = await validator.validate_url(
                session=mock_session,
                url="https://uconn.edu/resource",
                url_hash=f"nonhtml_hash_{content_type.replace('/', '_')}"
            )

            # Non-HTML content should be invalid even with 200 status
            assert result.status_code == 200
            assert result.content_type == content_type
            assert result.is_valid is False
            assert result.error_message == "Invalid response"

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_timeout_handling(self, mock_config):
        """Test proper timeout handling"""
        validator = URLValidator(mock_config)

        # Create a mock session that raises TimeoutError
        class TimeoutSession:
            def get(self, url, **kwargs):
                return self

            async def __aenter__(self):
                raise asyncio.TimeoutError("Request timed out")

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        timeout_session = TimeoutSession()

        result = await validator.validate_url(
            session=timeout_session,
            url="https://slow.uconn.edu",
            url_hash="timeout_hash"
        )

        # Verify timeout handling
        assert result.url == "https://slow.uconn.edu"
        assert result.url_hash == "timeout_hash"
        assert result.status_code == 0
        assert result.content_type == ""
        assert result.content_length == 0
        assert result.is_valid is False
        assert result.error_message == "Request timeout"
        assert result.response_time > 0

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_client_error_handling(self, mock_config):
        """Test handling of various aiohttp ClientError types"""
        validator = URLValidator(mock_config)

        client_errors = [
            aiohttp.ClientConnectionError("Connection failed"),
            aiohttp.ClientTimeout("Request timeout"),
            aiohttp.ClientResponseError(
                request_info=Mock(),
                history=(),
                status=None,
                message="Response error"
            ),
            aiohttp.ClientPayloadError("Payload error"),
            aiohttp.ClientSSLError("SSL verification failed"),
        ]

        for error in client_errors:
            class ErrorSession:
                def get(self, url, **kwargs):
                    return self

                async def __aenter__(self):
                    raise error

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

            error_session = ErrorSession()

            result = await validator.validate_url(
                session=error_session,
                url="https://error.uconn.edu",
                url_hash=f"error_hash_{type(error).__name__}"
            )

            # Verify error handling
            assert result.status_code == 0
            assert result.is_valid is False
            assert str(error) in result.error_message
            assert result.response_time > 0

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_redirect_handling(self, mock_config):
        """Test that redirects are properly followed and final URL is recorded"""
        validator = URLValidator(mock_config)

        # Mock response after redirect
        final_content = b"<html><head><title>Final Page</title></head></html>"
        mock_response = MockResponse(
            status=200,
            headers={'Content-Type': 'text/html'},
            content=final_content,
            url="https://www.uconn.edu/final"  # Final URL after redirect
        )

        mock_session = MockSession([mock_response])

        result = await validator.validate_url(
            session=mock_session,
            url="https://uconn.edu/redirect",  # Original URL
            url_hash="redirect_hash"
        )

        # Should record the final URL after redirect
        assert result.url == "https://www.uconn.edu/final"
        assert result.status_code == 200
        assert result.is_valid is True

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_content_length_handling(self, mock_config):
        """Test proper content length calculation"""
        validator = URLValidator(mock_config)

        test_cases = [
            # Case 1: Content-Length header present and accurate
            {
                "content": b"<html><body>Test content</body></html>",
                "header_length": "37",
                "expected_length": 37
            },
            # Case 2: Content-Length header missing
            {
                "content": b"<html><body>No header</body></html>",
                "header_length": None,
                "expected_length": 34
            },
            # Case 3: Content-Length header incorrect (should use actual length)
            {
                "content": b"<html><body>Wrong header</body></html>",
                "header_length": "999",
                "expected_length": 38
            },
        ]

        for i, case in enumerate(test_cases):
            headers = {'Content-Type': 'text/html'}
            if case["header_length"]:
                headers['Content-Length'] = case["header_length"]

            mock_response = MockResponse(
                status=200,
                headers=headers,
                content=case["content"],
                url=f"https://uconn.edu/test{i}"
            )

            mock_session = MockSession([mock_response])

            result = await validator.validate_url(
                session=mock_session,
                url=f"https://uconn.edu/test{i}",
                url_hash=f"length_hash_{i}"
            )

            assert result.content_length == case["expected_length"]

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_batch_validation_comprehensive(self, mock_config, temp_output_file):
        """Test comprehensive batch validation with mixed results"""
        # Update config to use temporary file
        mock_config.get_stage2_config.return_value['output_file'] = str(temp_output_file)
        validator = URLValidator(mock_config)

        # Create batch with mixed URLs
        batch_items = [
            BatchQueueItem(
                url="https://uconn.edu/success",
                url_hash="success_hash",
                source_stage="stage1",
                data={"type": "success"}
            ),
            BatchQueueItem(
                url="https://uconn.edu/notfound",
                url_hash="notfound_hash",
                source_stage="stage1",
                data={"type": "notfound"}
            ),
            BatchQueueItem(
                url="https://uconn.edu/timeout",
                url_hash="timeout_hash",
                source_stage="stage1",
                data={"type": "timeout"}
            ),
        ]

        # Mock responses for each URL
        success_response = MockResponse(
            status=200,
            headers={'Content-Type': 'text/html'},
            content=b"<html><body>Success</body></html>",
            url="https://uconn.edu/success"
        )

        notfound_response = MockResponse(
            status=404,
            headers={'Content-Type': 'text/html'},
            content=b"<html><body>Not Found</body></html>",
            url="https://uconn.edu/notfound"
        )

        # Simulate timeout for third request
        class BatchMockSession:
            def __init__(self):
                self.call_count = 0

            def get(self, url, **kwargs):
                if url == "https://uconn.edu/success":
                    return success_response
                elif url == "https://uconn.edu/notfound":
                    return notfound_response
                else:  # timeout case
                    return TimeoutResponse()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        class TimeoutResponse:
            async def __aenter__(self):
                raise asyncio.TimeoutError("Timeout")

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        # Patch aiohttp.ClientSession to return our mock
        with patch('aiohttp.ClientSession', return_value=BatchMockSession()):
            with patch('aiohttp.TCPConnector'):
                await validator.validate_batch(batch_items)

        # Verify output file contains all results
        assert temp_output_file.exists()

        results = []
        with open(temp_output_file, 'r') as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        assert len(results) == 3

        # Verify specific results
        success_result = next(r for r in results if r['url_hash'] == 'success_hash')
        assert success_result['status_code'] == 200
        assert success_result['is_valid'] is True

        notfound_result = next(r for r in results if r['url_hash'] == 'notfound_hash')
        assert notfound_result['status_code'] == 404
        assert notfound_result['is_valid'] is False

        timeout_result = next(r for r in results if r['url_hash'] == 'timeout_hash')
        assert timeout_result['status_code'] == 0
        assert timeout_result['is_valid'] is False
        assert "timeout" in timeout_result['error_message'].lower()

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_validate_from_file_comprehensive(self, mock_config, temp_output_file):
        """Test validation from file with realistic Stage 1 output"""
        # Create test input file with Stage 1 format
        input_file = temp_output_file.parent / "stage1_input.jsonl"

        stage1_data = [
            {
                "discovered_url": "https://uconn.edu/valid",
                "url_hash": "valid_hash_123",
                "title": "Valid Page",
                "links_found": 5
            },
            {
                "discovered_url": "https://uconn.edu/invalid",
                "url_hash": "invalid_hash_456",
                "title": "Invalid Page",
                "links_found": 0
            },
            # Invalid JSON line (should be skipped)
            "invalid json line",
            {
                "discovered_url": "https://uconn.edu/another",
                "url_hash": "another_hash_789",
                "title": "Another Page",
                "links_found": 3
            }
        ]

        with open(input_file, 'w') as f:
            for item in stage1_data:
                if isinstance(item, dict):
                    f.write(json.dumps(item) + '\n')
                else:
                    f.write(item + '\n')

        # Update config to use temp files
        mock_config.get_stage2_config.return_value['output_file'] = str(temp_output_file)
        mock_config.get_stage2_config.return_value['max_workers'] = 2  # Small batch size for testing

        validator = URLValidator(mock_config)

        # Mock responses
        responses = [
            MockResponse(200, {'Content-Type': 'text/html'}, b"<html>Valid</html>", "https://uconn.edu/valid"),
            MockResponse(404, {'Content-Type': 'text/html'}, b"<html>Not Found</html>", "https://uconn.edu/invalid"),
            MockResponse(200, {'Content-Type': 'text/html'}, b"<html>Another</html>", "https://uconn.edu/another"),
        ]

        class FileMockSession:
            def __init__(self):
                self.call_count = 0

            def get(self, url, **kwargs):
                if self.call_count < len(responses):
                    response = responses[self.call_count]
                    self.call_count += 1
                    return response
                return MockResponse(404)

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        with patch('aiohttp.ClientSession', return_value=FileMockSession()):
            with patch('aiohttp.TCPConnector'):
                processed_count = await validator.validate_from_file(input_file)

        # Should process 3 valid JSON lines (skip the invalid one)
        assert processed_count == 3

        # Verify output
        assert temp_output_file.exists()

        results = []
        with open(temp_output_file, 'r') as f:
            for line in f:
                if line.strip():
                    results.append(json.loads(line))

        assert len(results) == 3

        # Clean up
        input_file.unlink()

    @pytest.mark.critical
    @pytest.mark.asyncio
    async def test_session_configuration_and_limits(self, mock_config):
        """Test that session is configured with proper limits and timeouts"""
        validator = URLValidator(mock_config)

        # Test connector limits are properly calculated
        assert validator.connector_limit == min(validator.max_workers * 2, 100)
        assert validator.connector_limit == 20  # 10 * 2

        # Test session timeout configuration
        assert validator.session_timeout.total == 30

        # Mock the session creation to verify parameters
        batch_items = [
            BatchQueueItem("https://test.uconn.edu", "test_hash", "stage1", {})
        ]

        mock_response = MockResponse(200, {'Content-Type': 'text/html'}, b"<html></html>")

        with patch('aiohttp.TCPConnector') as mock_connector:
            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = MockSession([mock_response])
                mock_session_class.return_value = mock_session

                await validator.validate_batch(batch_items)

                # Verify TCPConnector was called with correct parameters
                mock_connector.assert_called_once_with(
                    limit=20,  # connector_limit
                    limit_per_host=10,
                    ttl_dns_cache=300
                )

                # Verify ClientSession was called with proper config
                mock_session_class.assert_called_once()
                call_args = mock_session_class.call_args
                assert 'connector' in call_args.kwargs
                assert 'timeout' in call_args.kwargs
                assert 'headers' in call_args.kwargs
                assert call_args.kwargs['headers']['User-Agent'] == 'UConn-Spider/1.0'

    @pytest.mark.performance
    @pytest.mark.asyncio
    async def test_concurrent_validation_performance(self, mock_config):
        """Test that concurrent validation maintains performance"""
        import time

        validator = URLValidator(mock_config)

        # Create large batch for performance testing
        batch_size = 50
        batch_items = []
        responses = []

        for i in range(batch_size):
            batch_items.append(
                BatchQueueItem(
                    url=f"https://uconn.edu/page{i}",
                    url_hash=f"hash_{i}",
                    source_stage="stage1",
                    data={"page": i}
                )
            )
            responses.append(
                MockResponse(
                    200,
                    {'Content-Type': 'text/html'},
                    f"<html><body>Page {i}</body></html>".encode()
                )
            )

        class PerfMockSession:
            def __init__(self):
                self.call_count = 0

            def get(self, url, **kwargs):
                response = responses[self.call_count % len(responses)]
                self.call_count += 1
                return response

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        start_time = time.perf_counter()

        with patch('aiohttp.ClientSession', return_value=PerfMockSession()):
            with patch('aiohttp.TCPConnector'):
                await validator.validate_batch(batch_items)

        duration = time.perf_counter() - start_time
        throughput = batch_size / duration

        # Performance baseline - should handle concurrent requests efficiently
        assert throughput > 100, f"Validation throughput too low: {throughput:.1f} URLs/sec"
        assert duration < 2.0, f"Batch validation took too long: {duration:.2f}s"


@pytest.mark.integration
class TestValidatorIntegrationRegression:
    """Integration tests for validator with real aiohttp behavior"""

    @pytest.mark.asyncio
    async def test_real_aiohttp_session_behavior(self, mock_config):
        """Test with real aiohttp session to catch integration issues"""
        validator = URLValidator(mock_config)

        # Test with localhost to avoid external dependencies
        # This assumes a test HTTP server or uses httpbin-style responses

        # Use a controlled test approach with timeout
        timeout_config = Mock()
        timeout_config.get_stage2_config.return_value = {
            'max_workers': 1,
            'timeout': 1,  # Very short timeout
            'output_file': 'test_output.jsonl'
        }
        timeout_config.get.return_value = 'Test-Agent'

        timeout_validator = URLValidator(timeout_config)

        # Test with a URL that should timeout (non-routable IP)
        result = await timeout_validator.validate_url(
            session=None,  # Will create real session
            url="http://192.0.2.1:80",  # RFC 5737 test IP (non-routable)
            url_hash="timeout_test"
        )

        # Should handle the connection error gracefully
        assert result.status_code == 0
        assert result.is_valid is False
        assert result.error_message is not None