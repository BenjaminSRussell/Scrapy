import unittest
from pathlib import Path
import json
import shutil
from datetime import datetime, timedelta

from src.orchestrator.analytics_engine import RequestAnalyticsEngine, DomainAnalytics
from src.common.request_infrastructure import RequestAttempt, RequestOutcome


class TestRequestAnalyticsEngine(unittest.TestCase):
    def setUp(self):
        self.analytics_dir = Path("test_analytics_data")
        self.analytics_dir.mkdir(exist_ok=True)
        self.engine = RequestAnalyticsEngine(analytics_dir=self.analytics_dir)

    def tearDown(self):
        if self.analytics_dir.exists():
            shutil.rmtree(self.analytics_dir)

    def test_analyze_domain_performance_with_single_success(self):
        """
        Test that analyze_domain_performance handles a single successful request without crashing.
        """
        domain = "example.com"
        request_attempt = RequestAttempt(
            url=f"https://{domain}/page1",
            timestamp=datetime.now(),
            outcome=RequestOutcome.SUCCESS,
            status_code=200,
            response_time=0.5,
            error_message=None,
            retry_attempt=0,
            headers_used={'User-Agent': 'test-agent'},
            user_agent='test-agent'
        )
        self.engine.log_request_attempt(request_attempt)

        # This should not raise a statistics.StatisticsError
        analytics = self.engine.analyze_domain_performance(domain)
        self.assertIsInstance(analytics, DomainAnalytics)
        self.assertEqual(analytics.total_requests, 1)
        self.assertEqual(analytics.success_rate, 100.0)
        # With only one data point, it should fall back to the default timeout
        self.assertEqual(analytics.optimal_timeout, 10.0)