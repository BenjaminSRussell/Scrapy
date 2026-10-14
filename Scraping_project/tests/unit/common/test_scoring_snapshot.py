"""Daily URL-scoring Parquet snapshots (#1103). Offline; no Redis and no lake."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from src.common.scoring_metrics import calculate_decay_score
from src.common.scoring_snapshot import (
    ARCHIVE_NAME,
    COLUMNS,
    compact_old_snapshots,
    export_scoring_snapshot,
    main,
)
from src.utils.url_canon import url_hash

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[4]
PROJECT = REPO / "Scraping_project"
FACULTY = "https://www.uconn.edu/research/faculty/"
DAY = date(2026, 10, 14)
TS = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _export(tmp_path: Path, **kwargs):
    kwargs.setdefault("output_dir", tmp_path)
    kwargs.setdefault("day", DAY)
    kwargs.setdefault("ts", TS)
    kwargs.setdefault("compact", False)
    return export_scoring_snapshot(**kwargs)


def _table(path: Path):
    table = pq.read_table(path)
    assert table.column_names == list(COLUMNS)
    return table


def test_cli_writes_readable_parquet(tmp_path: Path):
    urls = tmp_path / "urls.txt"
    urls.write_text(f"# comment\n\n{FACULTY}\n", encoding="utf-8")
    out = tmp_path / "exports"
    code = main(
        ["--urls", str(urls), "--output-dir", str(out), "--day", "20261014", "--ts", "2026-10-14T12:00:00Z", "--no-compact"]
    )
    assert code == 0
    table = _table(out / "scoring_20261014.parquet")
    assert table.num_rows == 1
    row = table.to_pylist()[0]
    assert row["url_hash"] == url_hash(FACULTY)
    assert row["score"] == 100.0
    assert row["stage"] == "stage1"
    assert row["ts"] == TS
    features = json.loads(row["features"])
    assert features["value_score"] == 100
    assert features["content_likelihood"] == "high"
    assert "high_value_pattern" in features["reasons"]
    assert "decay_score" not in features


def test_canonical_variants_share_one_row(tmp_path: Path):
    result = _export(
        tmp_path,
        urls=["https://WWW.UConn.edu/About/", "https://www.uconn.edu/about"],
    )
    assert result.written is True and result.rows == 1
    row = _table(result.path).to_pylist()[0]
    assert row["url_hash"] == url_hash("https://www.uconn.edu/about")
    # Same timestamp: the later URL wins. `/about` does not match the `/about/` pattern.
    assert row["score"] == 70.0
    assert json.loads(row["features"])["url"] == "https://www.uconn.edu/about"


def test_explicit_score_is_not_reassessed_and_decay_is_inside_features(tmp_path: Path):
    published = "2026-10-13T12:00:00Z"
    result = _export(
        tmp_path,
        records=[
            {
                "url": "https://www.uconn.edu/login",
                "score": 99,
                "stage": "stage2",
                "features": {"note": "manual"},
                "publication_date": published,
                "ts": "2026-10-14T12:00:00Z",
            }
        ],
    )
    row = _table(result.path).to_pylist()[0]
    assert row["score"] == 99.0
    assert row["stage"] == "stage2"
    features = json.loads(row["features"])
    assert features["note"] == "manual"
    assert "low_value_pattern" not in features.get("reasons", [])
    assert features["decay_score"] == pytest.approx(calculate_decay_score(published, reference_date=TS))
    assert features["url"] == "https://www.uconn.edu/login"


def test_assessed_decay_does_not_replace_value_score(tmp_path: Path):
    result = _export(
        tmp_path,
        records=[{"url": "https://example.com/a", "publication_date": "2026-10-13T12:00:00Z"}],
    )
    row = _table(result.path).to_pylist()[0]
    features = json.loads(row["features"])
    assert row["score"] == features["value_score"] == 60.0
    assert 0.0 < features["decay_score"] < 1.0


def test_bad_rows_are_skipped_and_a_total_failure_does_not_write(tmp_path: Path):
    partial = _export(
        tmp_path,
        records=[
            {"url": "https://example.com/ok", "score": 1, "stage": "stage2"},
            {"score": 1},
            {"url": "https://example.com/future", "publication_date": "2026-10-15T00:00:00Z"},
        ],
    )
    assert partial.written is True and partial.rows == 1 and partial.skipped == 2
    assert _table(partial.path).num_rows == 1

    failed = export_scoring_snapshot(
        records=[{"score": "nope"}, {"features": []}],
        output_dir=tmp_path / "empty",
        day=DAY,
        ts=TS,
        compact=False,
    )
    assert failed.written is False
    assert not (tmp_path / "empty").exists()


def test_same_day_merge_keeps_latest_timestamp(tmp_path: Path):
    first = _export(tmp_path, urls=[FACULTY, "https://example.com/old"])
    assert first.rows == 2
    second = _export(
        tmp_path,
        records=[
            {
                "url": FACULTY,
                "score": 15,
                "ts": "2026-10-14T18:00:00Z",
                "features": {"note": "later"},
            }
        ],
    )
    assert second.rows == 2
    rows = {row["url_hash"]: row for row in _table(second.path).to_pylist()}
    later = rows[url_hash(FACULTY)]
    assert later["score"] == 15.0
    assert json.loads(later["features"])["note"] == "later"
    assert url_hash("https://example.com/old") in rows

    replaced = _export(tmp_path, urls=["https://example.com/only"], merge=False)
    assert replaced.rows == 1
    assert _table(replaced.path).to_pylist()[0]["url_hash"] == url_hash("https://example.com/only")


def test_compact_archives_old_daily_files_only(tmp_path: Path):
    export_scoring_snapshot(urls=[FACULTY], output_dir=tmp_path, day=date(2020, 1, 1), ts=datetime(2020, 1, 1, tzinfo=UTC), compact=False)
    export_scoring_snapshot(
        records=[{"url": FACULTY, "score": 7, "ts": "2020-01-02T00:00:00Z"}],
        output_dir=tmp_path,
        day=date(2020, 1, 2),
        ts=datetime(2020, 1, 2, tzinfo=UTC),
        compact=False,
    )
    recent_day = DAY - timedelta(days=1)
    export_scoring_snapshot(
        urls=["https://example.com/recent"],
        output_dir=tmp_path,
        day=recent_day,
        ts=TS,
        compact=False,
    )
    unrelated = tmp_path / "notes.parquet"
    unrelated.write_bytes(b"leave me")
    bogus = tmp_path / "scoring_20261340.parquet"
    bogus.write_bytes(b"not a date")

    result = export_scoring_snapshot(
        urls=["https://example.com/today"],
        output_dir=tmp_path,
        day=DAY,
        ts=TS,
        keep_days=14,
        compact=True,
    )
    assert result.compacted_files == 2
    assert result.archive_path == tmp_path / ARCHIVE_NAME
    assert not (tmp_path / "scoring_20200101.parquet").exists()
    assert not (tmp_path / "scoring_20200102.parquet").exists()
    assert (tmp_path / f"scoring_{recent_day.strftime('%Y%m%d')}.parquet").is_file()
    assert (tmp_path / snapshot_name(DAY)).is_file()
    assert unrelated.read_bytes() == b"leave me"
    assert bogus.read_bytes() == b"not a date"

    archived = _table(tmp_path / ARCHIVE_NAME).to_pylist()
    assert len(archived) == 1
    assert archived[0]["score"] == 7.0
    assert archived[0]["ts"] == datetime(2020, 1, 2, tzinfo=UTC)

    before = (tmp_path / ARCHIVE_NAME).read_bytes()
    again, archive = compact_old_snapshots(tmp_path, today=DAY, keep_days=14)
    assert again == 0 and archive == tmp_path / ARCHIVE_NAME
    assert (tmp_path / ARCHIVE_NAME).read_bytes() == before


def test_unreadable_snapshot_is_not_deleted(tmp_path: Path):
    export_scoring_snapshot(
        urls=["https://example.com/old"],
        output_dir=tmp_path,
        day=date(2020, 1, 1),
        ts=datetime(2020, 1, 1, tzinfo=UTC),
        compact=False,
    )
    corrupt = tmp_path / "scoring_20200102.parquet"
    corrupt.write_bytes(b"not parquet")
    with pytest.raises(ValueError, match="schema"):
        compact_old_snapshots(tmp_path, today=DAY, keep_days=14)
    assert (tmp_path / "scoring_20200101.parquet").is_file()
    assert corrupt.read_bytes() == b"not parquet"
    assert not (tmp_path / ARCHIVE_NAME).exists()


def test_refuses_delta_lake_dlq_and_kafka_spill(tmp_path: Path):
    for name in ("delta_lake", "dlq", "kafka_spill"):
        lake = tmp_path / "data" / name
        lake.mkdir(parents=True)
        with pytest.raises(ValueError, match="refusing"):
            export_scoring_snapshot(urls=["https://example.com/a"], output_dir=lake, day=DAY, ts=TS, compact=False)
        assert list(lake.iterdir()) == []


def test_export_does_not_enable_historical_scoring(tmp_path: Path, monkeypatch):
    created: list[dict] = []
    from src.common.url_value_assessor import URLValueAssessor

    def wrapped(*args, **kwargs):
        created.append(kwargs)
        return URLValueAssessor(*args, **kwargs)

    monkeypatch.setattr("src.common.url_value_assessor.URLValueAssessor", wrapped)
    _export(tmp_path, urls=[FACULTY])
    assert created and created[0].get("use_historical_data") is False


def test_cli_usage_and_jsonl_skip(tmp_path: Path, capsys):
    assert main(["--output-dir", str(tmp_path)]) == 2
    jsonl = tmp_path / "rows.jsonl"
    jsonl.write_text('{"url": "https://example.com/a", "score": 1, "stage": "stage3"}\nnot-json\n', encoding="utf-8")
    code = main(
        ["--jsonl", str(jsonl), "--output-dir", str(tmp_path / "out"), "--day", "2026-10-14", "--ts", "2026-10-14T12:00:00Z", "--no-compact"]
    )
    assert code == 0
    table = _table(tmp_path / "out" / "scoring_20261014.parquet")
    assert table.num_rows == 1 and table.to_pylist()[0]["stage"] == "stage3"
    captured = capsys.readouterr()
    assert "jsonl line 2" in captured.err
    assert "scoring snapshot:" in captured.out


def test_script_help_and_docs_describe_the_schema():
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(PROJECT / "scripts" / "export_scoring_snapshot.py"), "--help"],
        cwd=PROJECT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0
    assert "scoring_YYYYMMDD.parquet" in proc.stdout

    text = (REPO / "docs" / "scoring_snapshot.md").read_text(encoding="utf-8")
    for column in COLUMNS:
        assert f"`{column}`" in text
    assert "scoring_YYYYMMDD.parquet" in text
    assert "scoring_archive.parquet" in text
    assert "cron" in text.lower()
    assert "use_historical_data=False" in text


def snapshot_name(day: date) -> str:
    return f"scoring_{day.strftime('%Y%m%d')}.parquet"
