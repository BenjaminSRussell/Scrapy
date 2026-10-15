"""Shared loader for the stage2→stage3 summary payload fixtures (#668).

Producer tests (``tests/unit/stage2``) and consumer tests (``tests/unit/stage3``)
both read ``tests/fixtures/stage2_stage3_summary/`` through this module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "stage2_stage3_summary"


def summary_fixture_text(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def summary_fixture_json(name: str) -> Any:
    return json.loads(summary_fixture_text(name))


def pages_contract() -> dict[str, Any]:
    """The stage2_page_analysis consumer contract the payload must match."""
    path = Path(__file__).resolve().parents[2] / "docs" / "contracts" / "pages.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))
