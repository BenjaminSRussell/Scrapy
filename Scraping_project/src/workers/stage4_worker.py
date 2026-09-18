"""Stage 4 entrypoint shim — delegates to ``src.stage4.stage4_worker``."""

from __future__ import annotations

import asyncio

from src.stage4.stage4_worker import run_stage4_worker


def main() -> None:
    asyncio.run(run_stage4_worker())


if __name__ == "__main__":
    main()
