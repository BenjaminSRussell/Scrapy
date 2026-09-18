"""Default container entrypoint for the scraping pipeline.

Dockerfile CMD uses ``python -m src.main``. This module delegates to the
existing pipeline orchestrator so the image starts without ModuleNotFoundError.
"""

from __future__ import annotations

import asyncio

from src.orchestrator.pipeline_orchestrator import main as orchestrator_main


def main() -> None:
    asyncio.run(orchestrator_main())


if __name__ == "__main__":
    main()
