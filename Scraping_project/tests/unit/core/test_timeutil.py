"""#244: timezone-aware UTC everywhere; no datetime.utcnow() calls left."""

import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from src.core.timeutil import utc_now, utc_now_iso

ROOT = Path(__file__).resolve().parents[3]


def test_utc_now_is_aware_utc():
    now = utc_now()
    assert now.tzinfo is timezone.utc
    assert now.utcoffset().total_seconds() == 0


def test_utc_now_iso_keeps_z_suffix_shape():
    s = utc_now_iso()
    assert s.endswith("Z") and "+00:00" not in s
    parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None and parsed.utcoffset().total_seconds() == 0


def test_metadata_pipeline_timestamp_is_utc():
    from src.pipelines import MetadataPipeline

    item = MetadataPipeline().process_item({"url": "https://u.edu"}, SimpleNamespace(name="scout"))
    stamp = datetime.fromisoformat(item["scraped_at_utc"].replace("Z", "+00:00"))
    assert stamp.utcoffset().total_seconds() == 0


def test_no_utcnow_calls_in_src_or_monitoring():
    offenders = []
    for base in ("src", "monitoring"):
        for path in (ROOT / base).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "utcnow"
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []
