"""
Core modules for UConn Web Scraping Pipeline

Consolidated, class-based architecture for better maintainability.
"""

from .metrics_system import MetricsCollector, PerformanceTracker
from .nlp_engine import NLPEngine, get_nlp_engine

__all__ = [
    'NLPEngine',
    'get_nlp_engine',
    'MetricsCollector',
    'PerformanceTracker'
]
