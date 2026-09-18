"""Stage 3 entrypoint shim — delegates to ``src.stage3.stage3_worker``.

Vendored from PR #262 (fix/142-docker-entrypoints).
"""

from __future__ import annotations


def main() -> None:
    import asyncio

    from src.stage3.stage3_worker import run_stage3_worker

    asyncio.run(run_stage3_worker())


if __name__ == "__main__":
    main()
