"""UConn Web Scraping Pipeline.

A three-stage async pipeline for discovering, validating, and enriching
web content from the uconn.edu domain.
"""

__version__ = "0.2.0"
__author__ = "Benjamin Russell"
__all__ = [
    "common",
    "orchestrator",
    "stage1",
    "stage2",
    "stage3",
]
