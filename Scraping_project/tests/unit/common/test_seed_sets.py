import pytest

from src.common import seed_sets
from src.common.seed_sets import SeedSetRegistry


@pytest.fixture
def raw_dir(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(seed_sets, "RAW_SEED_DIR", raw)
    return raw


def test_create_list_delete(tmp_path):
    reg = SeedSetRegistry(tmp_path / "seed_sets.json")
    seed_set, rejected = reg.create("admissions", ["https://a.example/", "https://b.example/", "https://a.example/"])
    assert seed_set.url_count == 2 and rejected == 0
    assert [s.name for s in reg.list_sets()] == ["admissions"]
    assert reg.get("admissions").urls == ["https://a.example/", "https://b.example/"]
    deleted = reg.delete("admissions")
    assert deleted.name == "admissions"
    assert reg.list_sets() == []


def test_create_rejects_duplicate_bad_name_and_empty(tmp_path):
    reg = SeedSetRegistry(tmp_path / "seed_sets.json")
    reg.create("core", ["https://a.example/"])
    with pytest.raises(ValueError, match="already exists"):
        reg.create("core", ["https://b.example/"])
    with pytest.raises(ValueError, match="invalid seed set name"):
        reg.create("bad name!", ["https://b.example/"])
    with pytest.raises(ValueError, match="no valid"):
        reg.create("empty", ["ftp://x", "  "])


def test_create_counts_rejected_urls(tmp_path):
    reg = SeedSetRegistry(tmp_path / "seed_sets.json")
    seed_set, rejected = reg.create("mixed", ["https://a.example/", "javascript:alert(1)"])
    assert seed_set.urls == ["https://a.example/"]
    assert rejected == 1


def test_mark_used_sets_last_used(tmp_path):
    reg = SeedSetRegistry(tmp_path / "seed_sets.json")
    reg.create("core", ["https://a.example/"])
    assert reg.get("core").last_used_at is None
    reg.mark_used("core")
    assert reg.get("core").last_used_at is not None


def test_unknown_set_raises_key_error(tmp_path):
    reg = SeedSetRegistry(tmp_path / "seed_sets.json")
    with pytest.raises(KeyError):
        reg.get("missing")
    with pytest.raises(KeyError):
        reg.delete("missing")
    with pytest.raises(KeyError):
        reg.mark_used("missing")


def test_import_csv_from_raw_dir(tmp_path, raw_dir):
    csv_path = raw_dir / "seeds.csv"
    csv_path.write_text("https://a.example/\n\nhttps://b.example/\n", encoding="utf-8")
    reg = SeedSetRegistry(tmp_path / "seed_sets.json")
    seed_set, rejected = reg.import_csv("imported", csv_path)
    assert seed_set.urls == ["https://a.example/", "https://b.example/"]
    assert seed_set.source == "csv:seeds.csv"
    assert rejected == 0


def test_import_csv_refuses_paths_outside_raw_dir(tmp_path, raw_dir):
    outside = tmp_path / "elsewhere.csv"
    outside.write_text("https://a.example/\n", encoding="utf-8")
    reg = SeedSetRegistry(tmp_path / "seed_sets.json")
    with pytest.raises(ValueError, match="must be under"):
        reg.import_csv("nope", outside)
    assert reg.list_sets() == []


def test_import_csv_missing_file(tmp_path, raw_dir):
    reg = SeedSetRegistry(tmp_path / "seed_sets.json")
    with pytest.raises(FileNotFoundError):
        reg.import_csv("nope", raw_dir / "missing.csv")
