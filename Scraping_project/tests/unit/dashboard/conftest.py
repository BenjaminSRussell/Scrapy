"""Put Scraping_project/dashboard on sys.path so ``import ccfeed`` works."""
from __future__ import annotations

import sys
from pathlib import Path

DASH = Path(__file__).resolve().parents[3] / "dashboard"
if str(DASH) not in sys.path:
    sys.path.insert(0, str(DASH))
