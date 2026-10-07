"""Default container entrypoint for the scraping pipeline.

Dockerfile CMD uses ``python -m src.main``. This module delegates to the
existing pipeline orchestrator so the image starts without ModuleNotFoundError.

Heavy imports are deferred into ``main()`` so ``import src.main`` stays a
lightweight smoke check (missing optional transitive deps must not break
module resolution).
"""

from __future__ import annotations


def main() -> None:
    import asyncio

    from src.orchestrator.pipeline_orchestrator import main as orchestrator_main

    asyncio.run(orchestrator_main())


if __name__ == "__main__":
    main()
