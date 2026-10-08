"""Stage 2 entrypoint shim — delegates to ``src.stage2.stage2_worker``."""

from __future__ import annotations


def main() -> None:
    import asyncio

    from src.stage2.stage2_worker import run_stage2_worker
    from src.utils.worker_metrics import start_worker_metrics_server

    start_worker_metrics_server("stage2")  # #789
    asyncio.run(run_stage2_worker())


if __name__ == "__main__":
    main()
