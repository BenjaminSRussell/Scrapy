"""Stage 1 (URL discovery) entrypoint used by docker-compose."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    # Deferred: PipelineOrchestrator pulls Stage 4 / optional deps at import time.
    from src.orchestrator.pipeline_orchestrator import PipelineOrchestrator

    from src.utils.logging_config import configure_logging

    configure_logging("stage1")  # #466: correlation fields + LOG_FORMAT=json (#238)
    logger.info("Starting Stage 1 worker (scout via PipelineOrchestrator)")
    PipelineOrchestrator().run_stage1()


if __name__ == "__main__":
    main()
