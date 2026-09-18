"""Stage 1 (URL discovery) entrypoint used by docker-compose.

Vendored from PR #262 (fix/142-docker-entrypoints).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    # Deferred: PipelineOrchestrator pulls Stage 4 / optional deps at import time.
    from src.orchestrator.pipeline_orchestrator import PipelineOrchestrator

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting Stage 1 worker (scout via PipelineOrchestrator)")
    PipelineOrchestrator().run_stage1()


if __name__ == "__main__":
    main()
