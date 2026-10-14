"""#282: kafka-delta-ingest commits offsets only after the Delta commit.

PR CI does not build the Rust crate, so guard the invariants textually; the
crate's own `cargo test` covers offset tracking and backoff.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAIN_RS = (ROOT / "kafka-delta-ingest" / "src" / "main.rs").read_text()


def test_rust_ingest_disables_auto_commit():
    assert '.set("enable.auto.commit", "false")' in MAIN_RS
    assert '"enable.auto.commit", "true"' not in MAIN_RS


def test_offsets_committed_only_after_successful_delta_write():
    # Accepted rows: commit sits in the write_batch Ok arm, after buffer.clear().
    ok_arm = re.search(r"Ok\(\(\)\) => \{(.*?)break;", MAIN_RS, re.S)
    assert ok_arm, "write_batch Ok arm not found"
    body = ok_arm.group(1)
    assert body.index("buffer.clear()") < body.index("pending_offsets.commit(&consumer)")
    # #177 adds one commit when the Delta buffer is empty so a poison offset is
    # not stuck until the next record. The write-failure arm still does not commit.
    assert MAIN_RS.count("pending_offsets.commit(") == 2
    empty_arm = MAIN_RS.split("if should_write && buffer.is_empty()", 1)[1]
    empty_arm = empty_arm.split("} else if should_write", 1)[0]
    assert empty_arm.count("pending_offsets.commit(") == 1
    err_arm = MAIN_RS.split('error!("Failed to write batch (attempt', 1)[1]
    err_arm = err_arm.split("tokio::time::sleep", 1)[0]
    assert "pending_offsets.commit(" not in err_arm
    assert "exiting without committing offsets" in err_arm


def test_exhausted_write_retries_exit_without_commit():
    assert "exiting without committing offsets" in MAIN_RS


def test_config_consumer_auto_commit_off():
    cfg = yaml.safe_load((ROOT / "config.yml").read_text())
    assert cfg["kafka"]["consumer"]["enable_auto_commit"] is False
