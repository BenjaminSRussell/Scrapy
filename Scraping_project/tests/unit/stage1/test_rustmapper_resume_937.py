"""rustmapper resume data-dir handoff (#937).

No crawl, no Redis, no lake. SeedManager is a fake. The second pass of an
unchanged data-dir must not JSON-parse artifacts already checkpointed.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from src.stage1 import rustmapper_resume as rr


class _Seeds:
    def __init__(self, *, land: bool = True):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.land = land

    def add_urls_to_seeds(self, urls, source_url, source_spider, **kwargs):
        batch = list(urls)
        self.calls.append(batch)
        self.kwargs.append(kwargs)
        n = len(batch) if self.land else 0
        return {
            "seed_inserted": n,
            "domain_inserted": 0,
            "uconn_inserted": 0,
            "stage2_enqueued": n,
            "rejected": 0,
        }


def _line(url: str) -> bytes:
    return (json.dumps({"url": url, "depth": 1, "status_code": 200}) + "\n").encode()


def _inject(root, seeds=None):
    seeds = seeds or _Seeds()
    result = rr.inject_resume(root, seeds)
    return result, seeds


def test_crc32c_matches_castagnoli_check_value():
    assert rr.crc32c(b"123456789") == 0xE3069283
    assert rr.crc32c(b"") == 0


def test_resume_injects_once_and_second_pass_does_not_reparse(tmp_path, monkeypatch):
    incremental = tmp_path / rr.INCREMENTAL_NAME
    incremental.write_bytes(_line("https://example.edu/a") + _line("https://example.edu/b"))
    (tmp_path / "crawler_state.redb").write_bytes(b"")

    first, seeds = _inject(tmp_path)
    assert first["injected"] == 2
    assert seeds.calls == [["https://example.edu/a", "https://example.edu/b"]]
    assert seeds.kwargs[0]["write_uconn_urls"] is True

    def _boom(*_args, **_kwargs):
        raise AssertionError("JSONL parsed on an unchanged resume")

    monkeypatch.setattr(rr, "_parse_from", _boom)
    second, seeds2 = _inject(tmp_path)
    assert second["injected"] == 0
    assert seeds2.calls == []
    assert second["status"] == "empty"


def test_appended_incremental_does_not_reparse_committed_prefix(tmp_path):
    bad = b"{not-json}\n"
    first = _line("https://example.edu/old")
    incremental = tmp_path / rr.INCREMENTAL_NAME
    incremental.write_bytes(bad + first)
    prefix = len(bad)
    (tmp_path / rr.CHECKPOINT_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "incremental_offset": prefix,
                "incremental_prefix_sha256": hashlib.sha256(bad).hexdigest(),
                "incremental_size": prefix,
                "snapshot_sha256": None,
                "snapshot_size": None,
                "urls_injected": 0,
            }
        ),
        encoding="utf-8",
    )

    plan = rr.plan_resume(tmp_path)
    assert plan.urls == ["https://example.edu/old"]

    extra = _line("https://example.edu/new")
    incremental.write_bytes(bad + first + extra)
    # Commit the first tail, then append again, to prove the bad prefix stays unread.
    rr.commit_resume(tmp_path, plan)
    plan2 = rr.plan_resume(tmp_path)
    assert plan2.urls == ["https://example.edu/new"]


def test_replaced_incremental_injects_only_unseen_urls(tmp_path):
    incremental = tmp_path / rr.INCREMENTAL_NAME
    incremental.write_bytes(_line("https://example.edu/a"))
    _inject(tmp_path)

    incremental.write_bytes(_line("https://example.edu/b") + _line("https://example.edu/a"))
    result, seeds = _inject(tmp_path)
    assert result["injected"] == 1
    assert seeds.calls == [["https://example.edu/b"]]


def test_unchanged_snapshot_is_not_reparsed(tmp_path, monkeypatch):
    snapshot = tmp_path / rr.SNAPSHOT_NAME
    snapshot.write_bytes(_line("https://example.edu/snap"))
    _inject(tmp_path)

    def _boom(*_args, **_kwargs):
        raise AssertionError("snapshot JSON parsed again")

    monkeypatch.setattr(rr, "_parse_from", _boom)
    result, seeds = _inject(tmp_path)
    assert result["injected"] == 0
    assert seeds.calls == []


def test_snapshot_rewrite_uses_incremental_delta_without_reparsing_snapshot(tmp_path, monkeypatch):
    snapshot = tmp_path / rr.SNAPSHOT_NAME
    incremental = tmp_path / rr.INCREMENTAL_NAME
    snapshot.write_bytes(_line("https://example.edu/old"))
    incremental.write_bytes(_line("https://example.edu/old"))
    _inject(tmp_path)

    snapshot.write_bytes(_line("https://example.edu/old") + _line("https://example.edu/new"))
    incremental.write_bytes(_line("https://example.edu/new"))

    real_parse = rr._parse_from

    def _parse_from(path, offset):
        if path.name == rr.SNAPSHOT_NAME:
            raise AssertionError("full snapshot was re-parsed")
        return real_parse(path, offset)

    monkeypatch.setattr(rr, "_parse_from", _parse_from)
    result, seeds = _inject(tmp_path)
    assert seeds.calls == [["https://example.edu/new"]]
    assert result["injected"] == 1


def test_corrupt_jsonl_line_fails_safe_and_does_not_inject(tmp_path):
    (tmp_path / rr.SNAPSHOT_NAME).write_bytes(b'{"nope": 1}\n')
    seeds = _Seeds()
    with pytest.raises(rr.RustmapperResumeError):
        rr.inject_resume(tmp_path, seeds)
    assert seeds.calls == []
    assert not (tmp_path / rr.CHECKPOINT_NAME).exists()


def test_wal_crc_mismatch_and_bad_offset_fail_safe(tmp_path):
    good = rr.encode_wal_record(1, 1, b"record")
    (tmp_path / "wal.log").write_bytes(good)
    (tmp_path / rr.SNAPSHOT_NAME).write_bytes(_line("https://example.edu/a"))
    (tmp_path / "wal.offset").write_bytes((len(good) + 5).to_bytes(8, "little"))
    with pytest.raises(rr.RustmapperResumeError, match="past wal.log"):
        rr.plan_resume(tmp_path)

    (tmp_path / "wal.offset").write_bytes(len(good).to_bytes(8, "little"))
    plan = rr.plan_resume(tmp_path)
    assert plan.urls == ["https://example.edu/a"]

    flipped = bytearray(good)
    flipped[-1] ^= 0xFF
    (tmp_path / "wal.log").write_bytes(bytes(flipped))
    with pytest.raises(rr.RustmapperResumeError, match="CRC"):
        rr.plan_resume(tmp_path)


def test_wal_offset_must_be_8_bytes_and_record_aligned(tmp_path):
    record = rr.encode_wal_record(2, 3, b"abc")
    (tmp_path / "wal.log").write_bytes(record + b"\x01")  # torn tail is fine past the checkpoint
    (tmp_path / "wal.offset").write_bytes(b"\x01\x02")
    with pytest.raises(rr.RustmapperResumeError, match="8 little-endian"):
        rr.plan_resume(tmp_path)

    (tmp_path / "wal.offset").write_bytes((len(record) - 1).to_bytes(8, "little"))
    with pytest.raises(rr.RustmapperResumeError, match="checkpoint"):
        rr.plan_resume(tmp_path)

    (tmp_path / "wal.offset").write_bytes(len(record).to_bytes(8, "little"))
    plan = rr.plan_resume(tmp_path)
    assert plan.urls == []


def test_missing_or_empty_data_dir_and_corrupt_cursor_fail_safe(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(rr.RustmapperResumeError, match="not a directory"):
        rr.plan_resume(missing)

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(rr.RustmapperResumeError, match="no resume artifacts"):
        rr.plan_resume(empty)

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "wal.log").write_bytes(b"")
    (bad / rr.CHECKPOINT_NAME).write_text("{", encoding="utf-8")
    with pytest.raises(rr.RustmapperResumeError, match="stage1_resume.json"):
        rr.plan_resume(bad)


def test_replaced_incremental_without_seen_set_fails_safe(tmp_path):
    payload = _line("https://example.edu/a")
    incremental = tmp_path / rr.INCREMENTAL_NAME
    incremental.write_bytes(payload)
    (tmp_path / rr.CHECKPOINT_NAME).write_text(
        json.dumps(
            {
                "version": 1,
                "incremental_offset": len(payload),
                "incremental_prefix_sha256": hashlib.sha256(payload).hexdigest(),
                "incremental_size": len(payload),
                "snapshot_sha256": None,
                "snapshot_size": None,
                "urls_injected": 1,
            }
        ),
        encoding="utf-8",
    )
    incremental.write_bytes(_line("https://example.edu/b"))
    with pytest.raises(rr.RustmapperResumeError, match="seen-set"):
        rr.plan_resume(tmp_path)


def test_throttle_does_not_advance_checkpoint(tmp_path):
    (tmp_path / rr.SNAPSHOT_NAME).write_bytes(_line("https://example.edu/a"))
    seeds = _Seeds(land=False)
    result = rr.inject_resume(tmp_path, seeds)
    assert result["status"] == "retry"
    assert result["injected"] == 0
    assert not (tmp_path / rr.CHECKPOINT_NAME).exists()

    seeds.land = True
    again = rr.inject_resume(tmp_path, seeds)
    assert again["injected"] == 1


def test_orchestrator_passes_resume_dir_and_does_not_start_scout(tmp_path, monkeypatch):
    from src.orchestrator import pipeline_orchestrator as po

    (tmp_path / rr.SNAPSHOT_NAME).write_bytes(_line("https://example.edu/a"))
    seeds = _Seeds()
    orch = po.PipelineOrchestrator(config={})

    def _no_crawler(*_args, **_kwargs):
        raise AssertionError("Scout crawl started on a resume data-dir")

    monkeypatch.setattr(po, "CrawlerProcess", _no_crawler)
    monkeypatch.setattr(
        orch,
        "inject_rustmapper_resume",
        lambda data_dir: rr.inject_resume(data_dir, seeds)["injected"],
    )

    assert orch.run_stage1(resume_data_dir=str(tmp_path)) == 1
    assert seeds.kwargs[0]["write_uconn_urls"] is True

    monkeypatch.setenv("RUSTMAPPER_RESUME_DATA_DIR", str(tmp_path / "from-env"))
    seen = []
    monkeypatch.setattr(orch, "inject_rustmapper_resume", lambda data_dir: seen.append(data_dir) or 0)
    assert orch.run_stage1() == 0
    assert seen == [str(tmp_path / "from-env")]

    explicit = []
    monkeypatch.setattr(orch, "inject_rustmapper_resume", lambda data_dir: explicit.append(data_dir) or 4)
    assert orch.run_stage1(resume_data_dir=str(tmp_path / "explicit")) == 4
    assert explicit == [str(tmp_path / "explicit")]


def test_orchestrator_corrupt_resume_does_not_start_scout(tmp_path, monkeypatch):
    from src.orchestrator import pipeline_orchestrator as po

    (tmp_path / "wal.offset").write_bytes(b"short")
    orch = po.PipelineOrchestrator(config={})

    def _no_crawler(*_args, **_kwargs):
        raise AssertionError("Scout crawl started after a corrupt data-dir")

    monkeypatch.setattr(po, "CrawlerProcess", _no_crawler)
    with pytest.raises(rr.RustmapperResumeError):
        orch.run_stage1(resume_data_dir=str(tmp_path))
