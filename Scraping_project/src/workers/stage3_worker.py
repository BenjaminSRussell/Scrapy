"""Stage 3 entrypoint shim — delegates to ``src.stage3.stage3_worker``."""

from __future__ import annotations


def main() -> None:
    import asyncio

    from src.stage3.stage3_worker import run_stage3_worker
    from src.utils.worker_metrics import start_worker_metrics_server

    start_worker_metrics_server("stage3")  # #789
    asyncio.run(run_stage3_worker())


if __name__ == "__main__":
    # Compose runs this module directly. Without this the root logger had no handler and
    # every INFO line from the stage 3 worker was dropped (#466).
    from src.utils.logging_config import configure_logging

    configure_logging("stage3")
    main()
