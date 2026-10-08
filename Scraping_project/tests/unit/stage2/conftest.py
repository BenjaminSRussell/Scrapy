"""Everything under tests/unit/stage2 is an offline unit test (#218):
`pytest tests/unit/stage2 -m unit` selects the whole Stage 2 suite."""
from pathlib import Path

import pytest

_HERE = Path(__file__).parent


def pytest_collection_modifyitems(config, items):
    for item in items:
        if _HERE in Path(str(item.fspath)).parents:
            item.add_marker(pytest.mark.unit)
            item.add_marker(pytest.mark.stage2)
