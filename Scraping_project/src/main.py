"""Default container entrypoint for the scraping pipeline.

Dockerfile CMD uses ``python -m src.main``. The run is controlled by the
``pipeline.*`` settings (env ``PIPELINE_MODE``, ``STAGE1_URL_LIMIT``,
``STAGE2_CONCURRENCY``, ``STAGE3_CONCURRENCY``; see config.yml):

- ``once`` (default): one pipeline pass, then exit 0. A failed pass raises, so
  the process exits non-zero and compose's ``restart: on-failure`` retries it.
- ``loop``: repeat a pass every ``loop_interval_seconds``. A failed pass is
  logged and retried on the next interval.
- ``disabled``: exit 0 without running anything.

Heavy imports are deferred into ``main()`` so ``import src.main`` stays a
lightweight smoke check (missing optional transitive deps must not break
module resolution).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def main() -> None:
    import asyncio
    import time

    from src.core.config import pipeline_run_settings
    from src.orchestrator.pipeline_orchestrator import PipelineRunError
    from src.orchestrator.pipeline_orchestrator import main as orchestrator_main

    settings = pipeline_run_settings()
    if settings.mode == "disabled":
        logging.basicConfig(level=logging.INFO)
        logger.info("PIPELINE_MODE=disabled: not running the pipeline")
        return
    if settings.mode == "once":
        asyncio.run(orchestrator_main(settings))
        return

    while True:
        try:
            asyncio.run(orchestrator_main(settings))
        except PipelineRunError:
            logger.exception("Pipeline pass failed; retrying in %ss", settings.loop_interval_seconds)
        time.sleep(settings.loop_interval_seconds)


if __name__ == "__main__":
    main()
