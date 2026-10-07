import pytest
from src.common.seed_ops import SeedRegistry


def test_add_list_disable_audit(tmp_path):
    reg = SeedRegistry(tmp_path / "seeds.json", tmp_path / "audit.jsonl")
    reg.add("https://example.com/a")
    with pytest.raises(ValueError, match="duplicate"):
        reg.add("https://example.com/a")
    rows = reg.list_seeds()
    assert len(rows) == 1 and rows[0].status == "active"
    reg.disable("https://example.com/a")
    assert reg.list_seeds()[0].status == "disabled"
    assert reg.list_seeds(include_disabled=False) == []
    audit = reg.read_audit()
    assert [a["action"] for a in audit] == ["add", "disable"]
