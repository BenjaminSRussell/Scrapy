#!/usr/bin/env python3
"""
UConn Web Scraping Pipeline - Main Entry Point

Single entrypoint that delegates to the orchestrator.
"""

import sys
from pathlib import Path

# Add src to Python path
src_dir = Path(__file__).parent / 'src'
sys.path.insert(0, str(src_dir))

if __name__ == "__main__":
    from orchestrator.main import main
    import asyncio
    sys.exit(asyncio.run(main()))