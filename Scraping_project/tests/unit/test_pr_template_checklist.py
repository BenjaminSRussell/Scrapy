"""#292: the PR template asks contributors what they ran and whether tests/fixtures changed."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

TEMPLATE = Path(__file__).resolve().parents[3] / ".github" / "pull_request_template.md"
CI = Path(__file__).resolve().parents[3] / ".github" / "workflows" / "main.yml"


@pytest.fixture(scope="module")
def text():
    return TEMPLATE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "needle",
    [
        "make test-unit",
        '-m "not slow and not kafka and not performance"',
        "Markers I used or added",
        "has tests",
        "offline",
        "fixtures",
        "Closes #N",
    ],
)
def test_template_checklist_items(text, needle):
    assert needle in text


def test_template_commands_match_ci(text):
    ci = CI.read_text(encoding="utf-8")
    for cmd in ("ruff check src/ --select F,E4,E7,E9", "not slow and not kafka and not performance"):
        assert cmd in ci and cmd in text


def test_checklist_items_are_checkboxes(text):
    section = text[text.index("## Tests"): text.index("## Compatibility")]
    items = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(items) == 3 and all(line.startswith("- [ ] ") for line in items)
