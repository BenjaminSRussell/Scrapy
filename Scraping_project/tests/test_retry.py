"""
Unit tests for retry and circuit breaker utilities.

Phase 9: Test resilience patterns.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from src.utils.retry import CircuitBreaker, with_retry
from src.core.exceptions import NetworkError, CircuitBreakerOpen, MaxRetriesExceeded


class TestCircuitBreaker:
    """Test CircuitBreaker class."""

    def test_circuit_closed_initially(self):
        """Test circuit breaker starts in closed state."""
        cb = CircuitBreaker(failure_threshold=3, name="test")
        assert cb.state == "closed"
        assert cb.can_execute() is True

    def test_circuit_opens_after_failures(self):
        """Test circuit opens after threshold failures."""
        cb = CircuitBreaker(failure_threshold=3, name="test")
        
        for _ in range(3):
            cb.record_failure()
        
        assert cb.state == "open"
        assert cb.can_execute() is False

    def test_circuit_half_open_after_timeout(self):
        """Test circuit enters half-open after recovery timeout."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0, name="test")
        
        # Open the circuit
        cb.record_failure()
        cb.record_failure()
        assert cb.state == "open"
        
        # Should enter half-open immediately (timeout=0)
        assert cb.can_execute() is True
        assert cb.state == "half-open"

    def test_circuit_closes_after_success(self):
        """Test circuit closes after successes in half-open."""
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0, name="test")
        
        # Open circuit
        cb.record_failure()
        cb.record_failure()
        
        # Enter half-open
        cb.can_execute()
        
        # Record successes
        cb.record_success()
        cb.record_success()
        
        assert cb.state == "closed"

    def test_get_state(self):
        """Test getting circuit breaker state."""
        cb = CircuitBreaker(name="test")
        state = cb.get_state()
        
        assert state["name"] == "test"
        assert state["state"] == "closed"
        assert state["failure_count"] == 0


@pytest.mark.asyncio
class TestRetryDecorator:
    """Test @with_retry decorator."""

    async def test_success_on_first_attempt(self):
        """Test no retry on success."""
        call_count = 0

        @with_retry(max_attempts=3)
        async def successful_function():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await successful_function()
        assert result == "success"
        assert call_count == 1

    async def test_retry_on_failure(self):
        """Test retry on transient failure."""
        call_count = 0

        @with_retry(max_attempts=3, base_delay=0.01, retry_on=(NetworkError,))
        async def failing_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise NetworkError("Temporary failure")
            return "success"

        result = await failing_function()
        assert result == "success"
        assert call_count == 3

    async def test_max_retries_exceeded(self):
        """Test MaxRetriesExceeded raised."""
        @with_retry(max_attempts=3, base_delay=0.01, retry_on=(NetworkError,))
        async def always_failing():
            raise NetworkError("Permanent failure")

        with pytest.raises(MaxRetriesExceeded):
            await always_failing()

    async def test_circuit_breaker_integration(self):
        """Test retry with circuit breaker."""
        cb = CircuitBreaker(failure_threshold=2, name="test")
        call_count = 0

        @with_retry(max_attempts=5, base_delay=0.01, circuit_breaker=cb, retry_on=(NetworkError,))
        async def failing_function():
            nonlocal call_count
            call_count += 1
            raise NetworkError("Failure")

        with pytest.raises(CircuitBreakerOpen):
            await failing_function()

        # Circuit should be open after 2 failures
        assert cb.state == "open"
