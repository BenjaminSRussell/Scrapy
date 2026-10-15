"""OpenSearch/Elasticsearch gold-summary export stays off unless asked (#687)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent


def _load():
    path = ROOT / "scripts" / "export_gold_summaries.py"
    spec = importlib.util.spec_from_file_location("export_gold_summaries_687", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve annotations via sys.modules (Python 3.11).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


exp = _load()


class Fake:
    def __init__(self, responses: list[tuple[int, bytes]]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, bytes | None]] = []

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url, body))
        if not self.responses:
            raise AssertionError(f"unexpected {method} {url}")
        status, payload = self.responses.pop(0)
        return exp.HttpResult(status, payload)


def _bulk_ok(count: int) -> bytes:
    items = [{"index": {"status": 201}} for _ in range(count)]
    return json.dumps({"errors": False, "items": items}).encode()


def _row(**overrides) -> dict:
    base = {
        "url": "https://example.com/test",
        "url_hash": "abc123",
        "summary": "A short summary of the page.",
        "word_count": 42,
        "keywords": ["summary", "test"],
        "quality_score": 0.75,
        "timestamp": "2026-01-02T03:04:05Z",
        "text_content": "raw page body that must not be indexed",
    }
    base.update(overrides)
    return base


def test_mapping_matches_the_fields_the_exporter_sends():
    mapping = exp.load_mapping()
    assert mapping["settings"]["number_of_replicas"] == 0
    assert mapping["mappings"]["dynamic"] == "strict"
    assert set(mapping["mappings"]["properties"]) == exp.MAPPED_FIELDS
    shipped = json.loads((ROOT / "scripts" / "search" / "gold_summaries_mapping.json").read_text(encoding="utf-8"))
    assert shipped == mapping


def test_documents_use_mapped_fields_only_and_keep_stage_identity():
    stage3 = exp.to_document(_row(keywords=[]), "stage3_summaries")
    assert stage3 is not None
    assert stage3["stage"] == "stage3"
    assert stage3["source_table"] == "stage3_summaries"
    assert "keywords" not in stage3
    assert "text_content" not in stage3
    assert stage3["quality_score"] == 0.75
    assert set(stage3) <= exp.MAPPED_FIELDS

    legacy = exp.to_document(_row(url_hash="legacy"), "stage4_summaries")
    assert legacy is not None and legacy["stage"] == "stage3"
    assert legacy["source_table"] == "stage4_summaries"

    stage4 = exp.to_document(
        {
            "url": "https://example.com/handbook.pdf",
            "url_hash": "pdf1",
            "summary": "Chapter summary for the large document.",
            "content_type": "application/pdf",
            "original_size": 1_000_000,
            "summary_size": 4000,
            "compression_ratio": 0.004,
            "is_pdf": False,
            "processed_at": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            "text_content": "do not index",
        },
        "stage4_large_doc_summaries",
    )
    assert stage4 is not None
    assert set(stage4) <= exp.MAPPED_FIELDS
    assert stage4["stage"] == "stage4"
    assert stage4["is_pdf"] is False
    assert stage4["summarized_at"] == "2026-01-02T03:04:05.000Z"
    assert exp.document_id(stage3) != exp.document_id(stage4)


@pytest.mark.parametrize(
    "row",
    [
        {"url": "https://example.com/a", "url_hash": "abc", "summary": "   "},
        {"url": "", "url_hash": "abc", "summary": "A short summary of the page."},
        {"url": "https://example.com/a", "url_hash": "bad\nid", "summary": "A short summary of the page."},
    ],
)
def test_unusable_rows_are_skipped(row):
    assert exp.to_document(row, "stage3_summaries") is None


def test_disabled_by_default_does_not_read_or_post(monkeypatch, tmp_path):
    monkeypatch.delenv("SEARCH_EXPORT_ENABLED", raising=False)
    assert "search_export_enabled" not in (ROOT / "config.yml").read_text(encoding="utf-8")
    from src.utils.feature_flags import FLAGS

    assert FLAGS["SEARCH_EXPORT_ENABLED"][0] is False
    missing = tmp_path / "no-such.jsonl"
    fake = Fake([])
    stats = exp.execute(
        exp.parse_args(["--dry-run", "--input", str(missing), "--url", "http://user:secret@127.0.0.1:9"]),
        transport=fake,
    )
    assert stats.enabled is False
    assert stats.documents == 0
    assert fake.calls == []


def test_flag_enables_a_dry_run_without_the_cli_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("SEARCH_EXPORT_ENABLED", "1")
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    out = tmp_path / "bulk.ndjson"
    fake = Fake([])
    stats = exp.execute(
        exp.parse_args(["--dry-run", "--input", str(path), "--output", str(out)]),
        transport=fake,
    )
    assert stats.enabled is True
    assert stats.documents == 1 and stats.indexed == 1 and stats.errors == 0
    assert fake.calls == []
    body = out.read_bytes()
    assert body.endswith(b"\n")
    lines = body.decode().splitlines()
    assert len(lines) == 2
    meta = json.loads(lines[0])
    doc = json.loads(lines[1])
    assert meta["index"] == {"_index": "gold-summaries", "_id": "stage3_summaries:abc123"}
    assert doc["summary"] == "A short summary of the page."
    assert "text_content" not in doc


def test_dry_run_batches_both_summary_tables_and_skips_blanks(tmp_path):
    rows = [
        _row(),
        {"url": "https://example.com/x", "url_hash": "nope", "summary": " "},
        {
            "source_table": "stage4_large_doc_summaries",
            "url": "https://example.com/handbook.pdf",
            "url_hash": "pdf1",
            "summary": "Chapter summary for the large document.",
            "content_type": "application/pdf",
            "is_pdf": True,
            "original_size": 10,
        },
    ]
    path = tmp_path / "rows.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    out = tmp_path / "bulk.ndjson"
    stats = exp.execute(
        exp.parse_args([
            "--enable",
            "--dry-run",
            "--input",
            str(path),
            "--output",
            str(out),
            "--batch-size",
            "1",
        ])
    )
    assert stats.skipped == 1
    assert stats.documents == 2 and stats.batches == 2
    ids = [json.loads(line)["index"]["_id"] for line in out.read_text(encoding="utf-8").splitlines()[::2]]
    assert ids == ["stage3_summaries:abc123", "stage4_large_doc_summaries:pdf1"]


def test_missing_lake_does_not_open_a_socket(tmp_path, monkeypatch):
    monkeypatch.setenv("DELTA_LAKE_PATH", str(tmp_path / "missing-lake"))
    fake = Fake([])
    with pytest.raises(exp.ExportError, match="lake not found"):
        exp.execute(exp.parse_args(["--enable"]), transport=fake)
    assert fake.calls == []


def test_missing_delta_tables_export_nothing(tmp_path):
    lake = tmp_path / "lake"
    lake.mkdir()
    out = tmp_path / "bulk.ndjson"
    stats = exp.execute(
        exp.parse_args(["--enable", "--dry-run", "--lake", str(lake), "--output", str(out)])
    )
    assert stats.documents == 0
    assert out.read_bytes() == b""


def test_enabled_export_creates_the_index_then_bulks(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(_row()) + "\n" + json.dumps(_row(url_hash="second")) + "\n", encoding="utf-8")
    fake = Fake([(404, b""), (200, b'{"acknowledged":true}'), (200, _bulk_ok(1)), (200, _bulk_ok(1))])
    stats = exp.execute(
        exp.parse_args(["--enable", "--input", str(path), "--batch-size", "1", "--url", "http://127.0.0.1:9200/"]),
        transport=fake,
    )
    assert [call[0] for call in fake.calls] == ["HEAD", "PUT", "POST", "POST"]
    assert fake.calls[0][1] == "http://127.0.0.1:9200/gold-summaries"
    assert fake.calls[2][1] == "http://127.0.0.1:9200/_bulk"
    assert stats.indexed == 2 and stats.errors == 0
    created = json.loads(fake.calls[1][2])
    assert created["mappings"]["dynamic"] == "strict"


def test_existing_index_is_not_recreated(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    fake = Fake([(200, b""), (200, _bulk_ok(1))])
    exp.execute(exp.parse_args(["--enable", "--input", str(path)]), transport=fake)
    assert [call[0] for call in fake.calls] == ["HEAD", "POST"]


def test_bulk_item_errors_are_counted(tmp_path, capsys):
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    error = {"errors": True, "items": [{"index": {"status": 400, "error": {"type": "mapper_parsing_exception"}}}]}
    fake = Fake([(200, b""), (200, json.dumps(error).encode())])
    stats = exp.execute(exp.parse_args(["--enable", "--input", str(path)]), transport=fake)
    assert stats.indexed == 0 and stats.errors == 1
    err = capsys.readouterr().err
    assert "mapper_parsing_exception" in err
    assert "A short summary" not in err


def test_credentials_stay_out_of_the_url(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    fake = Fake([])
    with pytest.raises(exp.ExportError, match="SEARCH_USER"):
        exp.execute(
            exp.parse_args(["--enable", "--input", str(path), "--url", "http://user:secret@127.0.0.1:9200"]),
            transport=fake,
        )
    assert fake.calls == []


def test_output_under_the_lake_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv("DELTA_LAKE_PATH", raising=False)
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    guarded = ROOT / "data" / "delta_lake" / "bulk.ndjson"
    with pytest.raises(exp.ExportError, match="refusing to write"):
        exp.execute(exp.parse_args(["--enable", "--dry-run", "--input", str(path), "--output", str(guarded)]))


def test_main_disabled_and_dry_run(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("SEARCH_EXPORT_ENABLED", raising=False)
    assert exp.main([]) == 0
    assert "disabled" in capsys.readouterr().out
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(_row()) + "\n", encoding="utf-8")
    out = tmp_path / "bulk.ndjson"
    assert exp.main(["--enable", "--dry-run", "--input", str(path), "--output", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["documents"] == 1 and summary["dry_run"] is True


def test_cli_process_stays_disabled(monkeypatch):
    monkeypatch.delenv("SEARCH_EXPORT_ENABLED", raising=False)
    env = {key: value for key, value in os.environ.items() if key != "SEARCH_EXPORT_ENABLED"}
    env["SEARCH_URL"] = "http://127.0.0.1:9"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_gold_summaries.py")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "disabled" in proc.stdout


def test_search_profile_is_opt_in_and_loopback_only():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["opensearch"]
    assert service["profiles"] == ["search"]
    assert service["image"] == "opensearchproject/opensearch:2.19.6"
    assert service["ports"] == ["127.0.0.1:9200:9200"]
    assert "DISABLE_SECURITY_PLUGIN=true" in service["environment"]
    assert "opensearch-data" in compose["volumes"]
    for name, other in compose["services"].items():
        if name == "opensearch":
            continue
        assert "search" not in other.get("profiles", [])

    start = (ROOT / "start.py").read_text(encoding="utf-8")
    assert 'setdefault("COMPOSE_PROFILES", "core,monitoring")' in start
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES=$${COMPOSE_PROFILES:-core,monitoring}" in makefile
    assert "core,monitoring,streaming,observability,ml,full,search" in makefile
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES=core,monitoring\n" in example
    assert re.search(r"(?m)^SEARCH_EXPORT_ENABLED\s*=\s*[1-9trueyeson]", example) is None
    lib = (ROOT / "scripts" / "compose_lib.sh").read_text(encoding="utf-8")
    assert 'COMPOSE_PROFILES="${COMPOSE_PROFILES:-core,monitoring}"' in lib
    reset = (ROOT / "scripts" / "complete_reset.sh").read_text(encoding="utf-8")
    infra = next(line for line in reset.splitlines() if line.startswith("INFRA="))
    assert "opensearch" in infra
    shutdown = (ROOT / "shutdown.py").read_text(encoding="utf-8")
    assert '"search"' in shutdown
    flags = (ROOT / "docs" / "FEATURE_FLAGS.md").read_text(encoding="utf-8")
    assert "SEARCH_EXPORT_ENABLED" in flags
    assert "off" in flags.lower()
