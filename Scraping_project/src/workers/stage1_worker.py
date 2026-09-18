"""Stage 1 (URL discovery) entrypoint used by docker-compose."""

from __future__ import annotations

import logging

from src.orchestrator.pipeline_orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting Stage 1 worker (scout via PipelineOrchestrator)")
    PipelineOrchestrator().run_stage1()


if __name__ == "__main__":
    main()
