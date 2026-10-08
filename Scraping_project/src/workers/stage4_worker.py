"""Stage 4 entrypoint shim — delegates to ``src.stage4.stage4_worker``."""

from __future__ import annotations


def main() -> None:
    import asyncio

    from src.stage4.stage4_worker import run_stage4_worker
    from src.utils.worker_metrics import start_worker_metrics_server

    start_worker_metrics_server("stage4")  # #789
    asyncio.run(run_stage4_worker())


if __name__ == "__main__":
    # Compose runs this module directly. Without this the root logger had no handler and
    # every INFO line from the stage 4 worker was dropped (#466).
    from src.utils.logging_config import configure_logging

    configure_logging("stage4")
    main()
