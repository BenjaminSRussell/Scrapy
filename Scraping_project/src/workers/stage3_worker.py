"""Stage 3 entrypoint shim — delegates to ``src.stage3.stage3_worker``."""

from __future__ import annotations


def main() -> None:
    import asyncio

    from src.stage3.stage3_worker import run_stage3_worker
    from src.utils.worker_metrics import start_worker_metrics_server

    start_worker_metrics_server("stage3")  # #789
    asyncio.run(run_stage3_worker())


if __name__ == "__main__":
    main()
