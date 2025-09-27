"""
Request Analytics and Learning Engine

Advanced analytics system that learns from request patterns to optimize:
- Success rates and failure patterns
- Response time optimization
- Error prediction and prevention
- Domain-specific adaptations
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from collections import defaultdict, Counter
from dataclasses import dataclass, asdict
import statistics

from common.request_infrastructure import RequestOutcome, RequestAttempt

logger = logging.getLogger(__name__)


@dataclass
class DomainAnalytics:
    """Analytics for a specific domain"""
    domain: str
    total_requests: int
    success_rate: float
    avg_response_time: float
    optimal_timeout: float
    optimal_delay: float
    common_errors: List[Tuple[str, int]]
    best_user_agents: List[Tuple[str, float]]
    peak_failure_times: List[str]
    last_updated: str


@dataclass
class RequestPattern:
    """Identified pattern in request behavior"""
    pattern_type: str
    description: str
    confidence: float
    recommendation: str
    affected_domains: List[str]


class RequestAnalyticsEngine:
    """Advanced analytics engine for request optimization"""

    def __init__(self, analytics_dir: Path = None):
        self.analytics_dir = analytics_dir or Path("data/analytics")
        self.analytics_dir.mkdir(parents=True, exist_ok=True)

        # Analytics files
        self.request_log = self.analytics_dir / "request_log.jsonl"
        self.domain_analytics = self.analytics_dir / "domain_analytics.json"
        self.patterns_file = self.analytics_dir / "learned_patterns.json"
        self.performance_trends = self.analytics_dir / "performance_trends.json"

        # In-memory caches
        self.recent_requests = []
        self.domain_stats = defaultdict(dict)
        self.identified_patterns = []

    def log_request_attempt(self, attempt: RequestAttempt):
        """Log a request attempt for analytics"""
        try:
            # Write to request log
            log_entry = {
                **asdict(attempt),
                'timestamp': attempt.timestamp.isoformat()
            }

            with open(self.request_log, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')

            # Add to recent requests cache
            self.recent_requests.append(attempt)
            if len(self.recent_requests) > 1000:
                self.recent_requests = self.recent_requests[-1000:]

        except Exception as e:
            logger.error(f"Error logging request attempt: {e}")

    def analyze_domain_performance(self, domain: str, days_back: int = 7) -> DomainAnalytics:
        """Analyze performance for a specific domain"""
        cutoff_time = datetime.now() - timedelta(days=days_back)
        domain_attempts = []

        # Load recent attempts for this domain
        try:
            if self.request_log.exists():
                with open(self.request_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            if domain in data.get('url', '') and datetime.fromisoformat(data['timestamp']) > cutoff_time:
                                domain_attempts.append(data)
        except Exception as e:
            logger.error(f"Error loading request log: {e}")

        if not domain_attempts:
            return DomainAnalytics(
                domain=domain,
                total_requests=0,
                success_rate=0.0,
                avg_response_time=0.0,
                optimal_timeout=10.0,
                optimal_delay=1.0,
                common_errors=[],
                best_user_agents=[],
                peak_failure_times=[],
                last_updated=datetime.now().isoformat()
            )

        # Calculate metrics
        total_requests = len(domain_attempts)
        successful_requests = len([a for a in domain_attempts if a['outcome'] == 'success'])
        success_rate = (successful_requests / total_requests) * 100 if total_requests > 0 else 0

        response_times = [a['response_time'] for a in domain_attempts if a['response_time'] > 0]
        avg_response_time = statistics.mean(response_times) if response_times else 0

        # Calculate optimal timeout (3x 95th percentile of successful response times)
        successful_times = [a['response_time'] for a in domain_attempts if a['outcome'] == 'success' and a['response_time'] > 0]
        if successful_times:
            optimal_timeout = max(5.0, statistics.quantile(successful_times, 0.95) * 3)
        else:
            optimal_timeout = 10.0

        # Calculate optimal delay based on rate limiting patterns
        rate_limited_attempts = [a for a in domain_attempts if a['outcome'] == 'rate_limited']
        if len(rate_limited_attempts) > total_requests * 0.1:  # More than 10% rate limited
            optimal_delay = 3.0  # Increase delay
        else:
            optimal_delay = 1.0

        # Analyze error patterns
        error_counter = Counter()
        for attempt in domain_attempts:
            if attempt['outcome'] != 'success':
                error_counter[attempt['outcome']] += 1
        common_errors = error_counter.most_common(5)

        # Analyze user agent performance
        ua_performance = defaultdict(list)
        for attempt in domain_attempts:
            if attempt['outcome'] == 'success':
                ua_performance[attempt['user_agent']].append(attempt['response_time'])

        best_user_agents = []
        for ua, times in ua_performance.items():
            if len(times) >= 3:  # At least 3 successful requests
                avg_time = statistics.mean(times)
                best_user_agents.append((ua, avg_time))
        best_user_agents.sort(key=lambda x: x[1])  # Sort by response time

        # Analyze failure time patterns
        failure_hours = defaultdict(int)
        for attempt in domain_attempts:
            if attempt['outcome'] != 'success':
                hour = datetime.fromisoformat(attempt['timestamp']).hour
                failure_hours[hour] += 1

        peak_failure_times = []
        if failure_hours:
            avg_failures = sum(failure_hours.values()) / 24
            peak_failure_times = [f"{hour:02d}:00" for hour, count in failure_hours.items() if count > avg_failures * 1.5]

        return DomainAnalytics(
            domain=domain,
            total_requests=total_requests,
            success_rate=success_rate,
            avg_response_time=avg_response_time,
            optimal_timeout=optimal_timeout,
            optimal_delay=optimal_delay,
            common_errors=common_errors,
            best_user_agents=best_user_agents[:3],  # Top 3
            peak_failure_times=peak_failure_times,
            last_updated=datetime.now().isoformat()
        )

    def identify_patterns(self) -> List[RequestPattern]:
        """Identify patterns in request behavior across all domains"""
        patterns = []

        # Load recent request data
        recent_data = []
        cutoff_time = datetime.now() - timedelta(days=7)

        try:
            if self.request_log.exists():
                with open(self.request_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            if datetime.fromisoformat(data['timestamp']) > cutoff_time:
                                recent_data.append(data)
        except Exception as e:
            logger.error(f"Error loading request log for pattern analysis: {e}")
            return patterns

        if len(recent_data) < 50:  # Need sufficient data
            return patterns

        # Pattern 1: High timeout domains
        domain_timeouts = defaultdict(list)
        for req in recent_data:
            if req['outcome'] == 'timeout':
                domain = req['url'].split('/')[2] if '//' in req['url'] else req['url']
                domain_timeouts[domain].append(req)

        high_timeout_domains = []
        for domain, timeouts in domain_timeouts.items():
            domain_requests = [r for r in recent_data if domain in r['url']]
            if len(domain_requests) > 10:
                timeout_rate = len(timeouts) / len(domain_requests)
                if timeout_rate > 0.3:  # More than 30% timeouts
                    high_timeout_domains.append(domain)

        if high_timeout_domains:
            patterns.append(RequestPattern(
                pattern_type="high_timeout_domains",
                description=f"Domains with high timeout rates: {', '.join(high_timeout_domains[:5])}",
                confidence=0.85,
                recommendation="Increase timeout values for these domains or implement progressive backoff",
                affected_domains=high_timeout_domains
            ))

        # Pattern 2: Rate limiting patterns
        rate_limited_domains = defaultdict(int)
        for req in recent_data:
            if req['outcome'] == 'rate_limited':
                domain = req['url'].split('/')[2] if '//' in req['url'] else req['url']
                rate_limited_domains[domain] += 1

        aggressive_domains = [domain for domain, count in rate_limited_domains.items() if count > 5]
        if aggressive_domains:
            patterns.append(RequestPattern(
                pattern_type="aggressive_rate_limiting",
                description=f"Domains with aggressive rate limiting: {', '.join(aggressive_domains[:5])}",
                confidence=0.90,
                recommendation="Implement longer delays and request spacing for these domains",
                affected_domains=aggressive_domains
            ))

        # Pattern 3: User agent blocking patterns
        ua_blocked = defaultdict(list)
        for req in recent_data:
            if req['outcome'] == 'blocked':
                ua_blocked[req['user_agent']].append(req)

        blocked_uas = [ua for ua, reqs in ua_blocked.items() if len(reqs) > 3]
        if blocked_uas:
            patterns.append(RequestPattern(
                pattern_type="user_agent_blocking",
                description=f"User agents frequently blocked: {len(blocked_uas)} different UAs",
                confidence=0.80,
                recommendation="Rotate user agents more frequently and use more diverse UA strings",
                affected_domains=list(set([req['url'].split('/')[2] for reqs in ua_blocked.values() for req in reqs if '//' in req['url']]))
            ))

        # Pattern 4: Time-based failure patterns
        hourly_failures = defaultdict(int)
        hourly_totals = defaultdict(int)
        for req in recent_data:
            hour = datetime.fromisoformat(req['timestamp']).hour
            hourly_totals[hour] += 1
            if req['outcome'] != 'success':
                hourly_failures[hour] += 1

        problematic_hours = []
        for hour in range(24):
            if hourly_totals[hour] > 5:  # At least 5 requests in that hour
                failure_rate = hourly_failures[hour] / hourly_totals[hour]
                if failure_rate > 0.5:  # More than 50% failure rate
                    problematic_hours.append(hour)

        if problematic_hours:
            patterns.append(RequestPattern(
                pattern_type="time_based_failures",
                description=f"High failure rates during hours: {', '.join([f'{h:02d}:00' for h in problematic_hours])}",
                confidence=0.75,
                recommendation="Schedule requests outside peak failure hours or implement time-based delays",
                affected_domains=[]
            ))

        # Pattern 5: SSL/TLS issues
        ssl_domains = defaultdict(int)
        for req in recent_data:
            if req['outcome'] == 'ssl_error':
                domain = req['url'].split('/')[2] if '//' in req['url'] else req['url']
                ssl_domains[domain] += 1

        ssl_problematic = [domain for domain, count in ssl_domains.items() if count > 2]
        if ssl_problematic:
            patterns.append(RequestPattern(
                pattern_type="ssl_certificate_issues",
                description=f"Domains with SSL/TLS issues: {', '.join(ssl_problematic[:5])}",
                confidence=0.95,
                recommendation="Implement more permissive SSL settings or certificate validation bypass for these domains",
                affected_domains=ssl_problematic
            ))

        self.identified_patterns = patterns
        self._save_patterns()
        return patterns

    def generate_optimization_recommendations(self) -> Dict[str, Any]:
        """Generate specific optimization recommendations based on analytics"""
        patterns = self.identify_patterns()

        recommendations = {
            'immediate_actions': [],
            'configuration_changes': {},
            'monitoring_alerts': [],
            'performance_optimizations': []
        }

        for pattern in patterns:
            if pattern.pattern_type == "high_timeout_domains":
                recommendations['configuration_changes']['timeout_overrides'] = {
                    domain: 30.0 for domain in pattern.affected_domains
                }
                recommendations['immediate_actions'].append(
                    f"Increase timeout for {len(pattern.affected_domains)} high-timeout domains"
                )

            elif pattern.pattern_type == "aggressive_rate_limiting":
                recommendations['configuration_changes']['delay_overrides'] = {
                    domain: 5.0 for domain in pattern.affected_domains
                }
                recommendations['immediate_actions'].append(
                    f"Implement longer delays for {len(pattern.affected_domains)} rate-limiting domains"
                )

            elif pattern.pattern_type == "user_agent_blocking":
                recommendations['immediate_actions'].append(
                    "Expand user agent rotation pool and increase rotation frequency"
                )
                recommendations['performance_optimizations'].append(
                    "Implement smart user agent selection based on domain success rates"
                )

            elif pattern.pattern_type == "time_based_failures":
                recommendations['monitoring_alerts'].append(
                    f"Monitor failure rates during identified peak failure hours"
                )
                recommendations['performance_optimizations'].append(
                    "Implement time-aware request scheduling"
                )

            elif pattern.pattern_type == "ssl_certificate_issues":
                recommendations['configuration_changes']['ssl_verification'] = {
                    domain: False for domain in pattern.affected_domains
                }

        # Add general recommendations based on overall performance
        total_recent = len(self.recent_requests)
        if total_recent > 0:
            success_rate = len([r for r in self.recent_requests if r.outcome == RequestOutcome.SUCCESS]) / total_recent
            if success_rate < 0.7:
                recommendations['immediate_actions'].append(
                    f"Overall success rate is low ({success_rate:.1%}). Review and apply domain-specific optimizations."
                )

        return recommendations

    def get_performance_dashboard(self) -> Dict[str, Any]:
        """Generate comprehensive performance dashboard data"""
        cutoff_time = datetime.now() - timedelta(days=7)
        recent_data = []

        # Load recent data
        try:
            if self.request_log.exists():
                with open(self.request_log, 'r', encoding='utf-8') as f:
                    for line in f:
                        if line.strip():
                            data = json.loads(line)
                            if datetime.fromisoformat(data['timestamp']) > cutoff_time:
                                recent_data.append(data)
        except Exception as e:
            logger.error(f"Error loading data for dashboard: {e}")

        if not recent_data:
            return {"error": "No recent data available"}

        # Calculate overall metrics
        total_requests = len(recent_data)
        successful_requests = len([r for r in recent_data if r['outcome'] == 'success'])
        success_rate = (successful_requests / total_requests) * 100

        response_times = [r['response_time'] for r in recent_data if r['response_time'] > 0]
        avg_response_time = statistics.mean(response_times) if response_times else 0

        # Domain breakdown
        domain_stats = defaultdict(lambda: {'requests': 0, 'successes': 0})
        for req in recent_data:
            domain = req['url'].split('/')[2] if '//' in req['url'] else 'unknown'
            domain_stats[domain]['requests'] += 1
            if req['outcome'] == 'success':
                domain_stats[domain]['successes'] += 1

        # Top domains by request count
        top_domains = sorted(domain_stats.items(), key=lambda x: x[1]['requests'], reverse=True)[:10]

        # Error analysis
        error_breakdown = Counter()
        for req in recent_data:
            if req['outcome'] != 'success':
                error_breakdown[req['outcome']] += 1

        # Hourly request distribution
        hourly_distribution = defaultdict(int)
        for req in recent_data:
            hour = datetime.fromisoformat(req['timestamp']).hour
            hourly_distribution[hour] += 1

        patterns = self.identify_patterns()
        recommendations = self.generate_optimization_recommendations()

        return {
            'overview': {
                'total_requests': total_requests,
                'success_rate': success_rate,
                'avg_response_time': avg_response_time,
                'time_period': '7 days'
            },
            'domain_performance': [
                {
                    'domain': domain,
                    'requests': stats['requests'],
                    'success_rate': (stats['successes'] / stats['requests']) * 100,
                    'successes': stats['successes']
                }
                for domain, stats in top_domains
            ],
            'error_breakdown': dict(error_breakdown.most_common(10)),
            'hourly_distribution': dict(hourly_distribution),
            'identified_patterns': [asdict(p) for p in patterns],
            'recommendations': recommendations,
            'last_updated': datetime.now().isoformat()
        }

    def _save_patterns(self):
        """Save identified patterns to file"""
        try:
            patterns_data = [asdict(p) for p in self.identified_patterns]
            with open(self.patterns_file, 'w') as f:
                json.dump({
                    'patterns': patterns_data,
                    'last_updated': datetime.now().isoformat()
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving patterns: {e}")

    def export_analytics_report(self, output_file: Path = None) -> Path:
        """Export comprehensive analytics report"""
        output_file = output_file or self.analytics_dir / f"analytics_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        dashboard_data = self.get_performance_dashboard()

        try:
            with open(output_file, 'w') as f:
                json.dump(dashboard_data, f, indent=2, ensure_ascii=False)

            logger.info(f"Analytics report exported to: {output_file}")
            return output_file

        except Exception as e:
            logger.error(f"Error exporting analytics report: {e}")
            raise