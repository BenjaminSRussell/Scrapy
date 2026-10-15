"""#941: rustmapper degraded-mode status strings, linked from Stage 1 job mode."""

from pathlib import Path

from src.stage1.rustmapper_degraded_copy import (
    INTRO,
    JOB_MODE_DOC,
    MODES,
    REASONS,
    RUNBOOK,
    format_job_mode_text,
)

REPO = Path(__file__).resolve().parents[4]


def test_status_strings_and_next_actions_are_enumerated():
    assert [mode["status"] for mode in MODES] == [
        "abort",
        "fallback_python",
        "resume_data_dir",
    ]
    assert [reason["status"] for reason in REASONS] == [
        "install_missing",
        "crawl_empty",
        "timeout_storm",
        "oom",
        "crash",
        "sitemap_jsonl_missing",
    ]
    mode_names = {mode["status"] for mode in MODES}
    seen = set()
    for mode in MODES:
        assert mode["when"].strip()
        assert mode["next_action"].strip()
        assert mode["next_action"] not in seen
        seen.add(mode["next_action"])
    for reason in REASONS:
        assert reason["mode"] in mode_names
        assert reason["next_action"].strip()
        assert reason["next_action"] not in seen
        seen.add(reason["next_action"])


def test_stage1_job_mode_links_runbook_and_cc_cli_share_copy():
    job_mode = (REPO / JOB_MODE_DOC).read_text(encoding="utf-8")
    runbook = (REPO / RUNBOOK).read_text(encoding="utf-8")
    js = (REPO / "Scraping_project/dashboard/format-utils.js").read_text(encoding="utf-8")
    app = (REPO / "Scraping_project/dashboard/app.js").read_text(encoding="utf-8")
    html = (REPO / "Scraping_project/dashboard/index.html").read_text(encoding="utf-8")
    cli = (REPO / "Scraping_project/cli.py").read_text(encoding="utf-8")
    text = format_job_mode_text()

    assert "Stage 1 job mode" in job_mode
    assert "rustmapper_discovery_degraded.md" in job_mode
    assert "JOB_MODE.md" in runbook
    assert INTRO in runbook
    assert text.startswith("Stage 1 job mode")
    assert INTRO in text

    for blob in (runbook, js, text):
        for mode in MODES:
            assert mode["status"] in blob
            assert mode["when"] in blob
            assert mode["next_action"] in blob
        for reason in REASONS:
            assert reason["status"] in blob
            assert reason["next_action"] in blob

    assert 'id="stage-drilldown-job-mode"' in html
    assert "renderStage1JobMode" in app
    assert "Stage 1 job mode" in app
    assert "def cmd_stage1_job_mode" in cli
    assert '"job-mode"' in cli
    assert '"stage1"' in cli
