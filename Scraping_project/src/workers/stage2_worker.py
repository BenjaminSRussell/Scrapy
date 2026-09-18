"""Stage 2 entrypoint shim — delegates to ``src.stage2.stage2_worker``."""

from __future__ import annotations

import asyncio

from src.stage2.stage2_worker import run_stage2_worker


def main() -> None:
    asyncio.run(run_stage2_worker())


if __name__ == "__main__":
    main()
