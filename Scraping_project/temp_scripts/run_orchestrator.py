#!/usr/bin/env python

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

from src.orchestrator import PipelineOrchestrator

async def main():
    print("\n" + "🚀 " * 40)
    print("PIPELINE ORCHESTRATOR - FULL EXECUTION")
    print("🚀 " * 40 + "\n")

    orchestrator = PipelineOrchestrator()

    await orchestrator.run_full_pipeline(
        stage1_url_limit=20,
        stage2_concurrent=5,
        stage3_concurrent=3,
    )

    print("\n✅ Pipeline execution complete!")
    print(f"Check Delta Lake tables for results:")
    print(f"  - data/delta_lake/stage2_queue/")
    print(f"  - data/delta_lake/stage2_page_analysis/")
    print(f"  - data/delta_lake/stage3_summaries/")
    print(f"  - data/delta_lake/stage4_large_doc_summaries/")

if __name__ == "__main__":
    import os
    os.chdir(project_root)
    asyncio.run(main())
