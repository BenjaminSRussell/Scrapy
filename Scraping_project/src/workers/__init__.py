"""Docker/compose entrypoint shims for stage workers.

Real Stage 2-4 workers live under ``src.stage2`` / ``src.stage3`` / ``src.stage4``.
Stage 1 is driven by Scrapy spiders via ``PipelineOrchestrator.run_stage1``.
These modules exist so ``python -m src.workers.stageN_worker`` resolves.
"""
