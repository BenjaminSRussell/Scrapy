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

def main():
    if len(sys.argv) < 2:
        print("Usage: python run_individual_stage.py <stage1|stage2|stage3|stage4>")
        sys.exit(1)

    stage = sys.argv[1].lower()

    if stage not in ["stage1", "stage2", "stage3", "stage4"]:
        print(f"Error: Invalid stage '{stage}'")
        print("Valid stages: stage1, stage2, stage3, stage4")
        sys.exit(1)

    print(f"\n🚀 Running {stage.upper()} only...\n")

    orchestrator = PipelineOrchestrator()

    try:
        result = orchestrator.run_stage_by_name(stage)
        print(f"\n✅ {stage.upper()} completed successfully!")
        if result:
            print(f"Processed: {result} items")
    except Exception as e:
        print(f"\n❌ {stage.upper()} failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    import os
    os.chdir(project_root)
    main()
