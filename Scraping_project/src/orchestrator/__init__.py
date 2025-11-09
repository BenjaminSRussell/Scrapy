"""Pipeline Orchestrator Module

Coordinates all 4 stages of the scraping pipeline:
- Stage 1: URL Discovery (Scout Spider)
- Stage 2: Page Analysis
- Stage 3: Summarization (quality docs)
- Stage 4: Large Document Processing
"""

from src.orchestrator.pipeline_orchestrator import PipelineOrchestrator, PipelineStats

__all__ = ["PipelineOrchestrator", "PipelineStats"]
