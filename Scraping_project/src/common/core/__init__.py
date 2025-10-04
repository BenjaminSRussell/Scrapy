"""
Core modules for UConn Web Scraping Pipeline

Consolidated, class-based architecture for better maintainability.
"""

from .nlp_engine import NLPEngine, get_nlp_engine
from .metrics_system import MetricsCollector, PerformanceTracker

__all__ = [
    'NLPEngine',
    'get_nlp_engine',
    'MetricsCollector',
    'PerformanceTracker'
]
