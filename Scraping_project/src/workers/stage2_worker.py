"""Stage 2 entrypoint shim — delegates to ``src.stage2.stage2_worker``.

Vendored from PR #262 (fix/142-docker-entrypoints).
"""

from __future__ import annotations


def main() -> None:
    import asyncio

    from src.stage2.stage2_worker import run_stage2_worker

    asyncio.run(run_stage2_worker())


if __name__ == "__main__":
    main()
