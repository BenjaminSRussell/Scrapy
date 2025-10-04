"""
Advanced Request Infrastructure with Learning and Adaptive Error Handling

This module provides a comprehensive system for handling HTTP requests with:
- Adaptive retry strategies based on error patterns
- Learning from failure patterns to improve success rates
- Request analytics and performance monitoring
- Automatic request optimization based on historical data
"""

import asyncio
import json
import logging
import random
import ssl
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class RequestOutcome(Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    SSL_ERROR = "ssl_error"
    HTTP_ERROR = "http_error"
    BLOCKED = "blocked"
    RATE_LIMITED = "rate_limited"
    DNS_ERROR = "dns_error"
    UNKNOWN_ERROR = "unknown_error"


@dataclass
class RequestAttempt:
    """Record of a single request attempt"""
    url: str
    timestamp: datetime
    outcome: RequestOutcome
    status_code: int | None
    response_time: float
    error_message: str | None
    retry_attempt: int
    headers_used: dict[str, str]
    user_agent: str


@dataclass
class RequestResult:
    """Final result of a request with all attempts"""
    url: str
    success: bool
    final_status_code: int | None
    content: str | None
    content_type: str | None
    content_length: int
    total_attempts: int
    total_time: float
    attempts: list[RequestAttempt]
    learned_optimizations: list[str]


class AdaptiveRequestConfig:
    """Configuration that learns and adapts based on request patterns"""

    def __init__(self, analytics_file: Path = None):
        self.analytics_file = analytics_file or Path("data/analytics/request_analytics.json")
        self.analytics_file.parent.mkdir(parents=True, exist_ok=True)

        # Base configuration
        self.base_timeout = 10
        self.max_retries = 5
        self.base_delay = 1.0
        self.max_delay = 30.0

        # TODO: The user agents are hardcoded. They should be configurable.
        # User agents pool (rotate to avoid detection)
        self.user_agents = [
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0"
        ]

        # Domain-specific learned configurations
        self.domain_configs = defaultdict(dict)
        self.error_patterns = defaultdict(list)
        self.success_patterns = defaultdict(list)

        # TODO: The recent request history is not saved. It should be saved to a file to persist it across runs.
        # Recent request history for pattern analysis
        self.recent_requests = deque(maxlen=1000)

        self.load_analytics()

    def load_analytics(self):
        """Load historical analytics to inform configuration"""
        if not self.analytics_file.exists():
            return

        try:
            with open(self.analytics_file) as f:
                data = json.load(f)

            self.domain_configs = defaultdict(dict, data.get('domain_configs', {}))
            self.error_patterns = defaultdict(list, data.get('error_patterns', {}))
            self.success_patterns = defaultdict(list, data.get('success_patterns', {}))

            logger.info(f"Loaded analytics for {len(self.domain_configs)} domains")

        except Exception as e:
            logger.warning(f"Failed to load analytics: {e}")

    def save_analytics(self):
        """Save current analytics data"""
        try:
            data = {
                'domain_configs': dict(self.domain_configs),
                'error_patterns': dict(self.error_patterns),
                'success_patterns': dict(self.success_patterns),
                'last_updated': datetime.now().isoformat()
            }

            with open(self.analytics_file, 'w') as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to save analytics: {e}")

    def get_optimal_config(self, domain: str) -> dict[str, Any]:
        """Get optimized configuration for a specific domain"""
        base_config = {
            'timeout': self.base_timeout,
            'max_retries': self.max_retries,
            'delay': self.base_delay,
            'user_agent': random.choice(self.user_agents)
        }

        # Apply domain-specific optimizations
        if domain in self.domain_configs:
            domain_opts = self.domain_configs[domain]
            base_config.update(domain_opts)

        return base_config

    def learn_from_attempt(self, attempt: RequestAttempt):
        """Learn from request attempt to improve future requests"""
        domain = attempt.url.split('/')[2] if '//' in attempt.url else attempt.url

        self.recent_requests.append(attempt)

        if attempt.outcome == RequestOutcome.SUCCESS:
            self.success_patterns[domain].append({
                'user_agent': attempt.user_agent,
                'headers': attempt.headers_used,
                'response_time': attempt.response_time
            })

            # Optimize timeout based on successful response times
            if domain not in self.domain_configs:
                self.domain_configs[domain] = {}

            success_times = [p['response_time'] for p in self.success_patterns[domain][-10:]]
            if success_times:
                # Set timeout to 3x average successful response time, min 5s
                avg_time = sum(success_times) / len(success_times)
                optimal_timeout = max(5, avg_time * 3)
                self.domain_configs[domain]['timeout'] = optimal_timeout

        else:
            self.error_patterns[domain].append({
                'outcome': attempt.outcome.value,
                'error_message': attempt.error_message,
                'user_agent': attempt.user_agent,
                'headers': attempt.headers_used
            })

            # Adjust retry strategy based on error patterns
            error_types = [p['outcome'] for p in self.error_patterns[domain][-10:]]

            # If many timeouts, increase timeout and reduce retries
            if error_types.count('timeout') > 5:
                self.domain_configs[domain]['timeout'] = min(30, self.base_timeout * 2)
                self.domain_configs[domain]['max_retries'] = max(2, self.max_retries - 1)

            # If many rate limits, increase delays
            if error_types.count('rate_limited') > 3:
                self.domain_configs[domain]['delay'] = min(10, self.base_delay * 3)


class SmartRequestHandler:
    """Intelligent request handler with adaptive strategies"""

    def __init__(self, config: AdaptiveRequestConfig = None):
        self.config = config or AdaptiveRequestConfig()
        self.session = None
        self.stats = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'total_retries': 0,
            'avg_response_time': 0.0
        }

    async def __aenter__(self):
        await self.create_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close_session()

    async def create_session(self):
        """Create optimized aiohttp session"""
        # Permissive SSL for development
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=20,
            ttl_dns_cache=300,
            ssl=ssl_context,
            enable_cleanup_closed=True,
            use_dns_cache=True
        )

        timeout = aiohttp.ClientTimeout(total=30)

        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            trust_env=True
        )

    async def close_session(self):
        """Clean up session"""
        if self.session:
            await self.session.close()
            self.session = None

    def _classify_error(self, exception: Exception) -> RequestOutcome:
        """Classify error type for learning purposes"""
        if isinstance(exception, asyncio.TimeoutError):
            return RequestOutcome.TIMEOUT
        elif isinstance(exception, aiohttp.ClientConnectionError):
            return RequestOutcome.CONNECTION_ERROR
        elif isinstance(exception, aiohttp.ClientSSLError):
            return RequestOutcome.SSL_ERROR
        elif isinstance(exception, aiohttp.ClientResponseError):
            if exception.status == 429:
                return RequestOutcome.RATE_LIMITED
            elif exception.status in [403, 406, 451]:
                return RequestOutcome.BLOCKED
            else:
                return RequestOutcome.HTTP_ERROR
        else:
            return RequestOutcome.UNKNOWN_ERROR

    def _get_smart_headers(self, domain: str, attempt_num: int) -> dict[str, str]:
        """Generate smart headers based on domain and attempt"""
        config = self.config.get_optimal_config(domain)

        headers = {
            'User-Agent': config['user_agent'],
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        }

        # Add randomization on retries to avoid fingerprinting
        if attempt_num > 0:
            headers['User-Agent'] = random.choice(self.config.user_agents)
            if attempt_num > 2:
                headers['X-Forwarded-For'] = f"{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}.{random.randint(1,254)}"

        return headers

    async def fetch_with_learning(self, url: str, method: str = 'GET') -> RequestResult:
        """Fetch URL with adaptive learning and error handling"""
        domain = url.split('/')[2] if '//' in url else url
        config = self.config.get_optimal_config(domain)

        attempts = []
        start_time = time.time()

        for attempt_num in range(config['max_retries']):
            attempt_start = time.time()
            headers = self._get_smart_headers(domain, attempt_num)

            try:
                # Progressive delay with jitter
                if attempt_num > 0:
                    delay = config['delay'] * (2 ** (attempt_num - 1))
                    jitter = random.uniform(0.1, 0.5)
                    await asyncio.sleep(delay + jitter)

                async with self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=config['timeout']),
                    allow_redirects=True
                ) as response:

                    content = await response.read()
                    content_str = content.decode('utf-8', errors='ignore')
                    response_time = time.time() - attempt_start

                    attempt = RequestAttempt(
                        url=url,
                        timestamp=datetime.now(),
                        outcome=RequestOutcome.SUCCESS,
                        status_code=response.status,
                        response_time=response_time,
                        error_message=None,
                        retry_attempt=attempt_num,
                        headers_used=headers,
                        user_agent=headers['User-Agent']
                    )

                    attempts.append(attempt)
                    self.config.learn_from_attempt(attempt)

                    # Update stats
                    self.stats['total_requests'] += 1
                    self.stats['successful_requests'] += 1
                    self.stats['total_retries'] += attempt_num

                    return RequestResult(
                        url=url,
                        success=True,
                        final_status_code=response.status,
                        content=content_str,
                        content_type=response.headers.get('Content-Type', ''),
                        content_length=len(content),
                        total_attempts=len(attempts),
                        total_time=time.time() - start_time,
                        attempts=attempts,
                        learned_optimizations=self._get_optimizations_for_domain(domain)
                    )

            except Exception as e:
                response_time = time.time() - attempt_start
                outcome = self._classify_error(e)

                attempt = RequestAttempt(
                    url=url,
                    timestamp=datetime.now(),
                    outcome=outcome,
                    status_code=None,
                    response_time=response_time,
                    error_message=str(e),
                    retry_attempt=attempt_num,
                    headers_used=headers,
                    user_agent=headers['User-Agent']
                )

                attempts.append(attempt)
                self.config.learn_from_attempt(attempt)

                logger.warning(f"Attempt {attempt_num + 1} failed for {url}: {outcome.value} - {e}")

        # All attempts failed
        self.stats['total_requests'] += 1
        self.stats['failed_requests'] += 1
        self.stats['total_retries'] += len(attempts)

        return RequestResult(
            url=url,
            success=False,
            final_status_code=None,
            content=None,
            content_type=None,
            content_length=0,
            total_attempts=len(attempts),
            total_time=time.time() - start_time,
            attempts=attempts,
            learned_optimizations=self._get_optimizations_for_domain(domain)
        )

    def _get_optimizations_for_domain(self, domain: str) -> list[str]:
        """Get list of learned optimizations for a domain"""
        optimizations = []

        if domain in self.config.domain_configs:
            config = self.config.domain_configs[domain]
            if 'timeout' in config:
                optimizations.append(f"Optimized timeout: {config['timeout']}s")
            if 'delay' in config:
                optimizations.append(f"Learned delay: {config['delay']}s")
            if 'max_retries' in config:
                optimizations.append(f"Adjusted retries: {config['max_retries']}")

        return optimizations

    def get_performance_summary(self) -> dict[str, Any]:
        """Get performance and learning summary"""
        success_rate = 0
        if self.stats['total_requests'] > 0:
            success_rate = (self.stats['successful_requests'] / self.stats['total_requests']) * 100

        return {
            'total_requests': self.stats['total_requests'],
            'success_rate': f"{success_rate:.1f}%",
            'total_retries': self.stats['total_retries'],
            'avg_retries_per_request': self.stats['total_retries'] / max(1, self.stats['total_requests']),
            'domains_learned': len(self.config.domain_configs),
            'error_patterns_tracked': len(self.config.error_patterns)
        }


# Export main classes
__all__ = [
    'RequestOutcome',
    'RequestAttempt',
    'RequestResult',
    'AdaptiveRequestConfig',
    'SmartRequestHandler'
]