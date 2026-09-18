"""Stage 3 entrypoint shim — delegates to ``src.stage3.stage3_worker``."""

from __future__ import annotations

import asyncio

from src.stage3.stage3_worker import run_stage3_worker


def main() -> None:
    asyncio.run(run_stage3_worker())


if __name__ == "__main__":
    main()
