"""Stage 4 entrypoint shim — delegates to ``src.stage4.stage4_worker``."""

from __future__ import annotations


def main() -> None:
    import asyncio

    from src.stage4.stage4_worker import run_stage4_worker

    asyncio.run(run_stage4_worker())


if __name__ == "__main__":
    main()
