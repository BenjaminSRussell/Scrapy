"""Operator copy for rustmapper discovery degraded modes (#941).

Stage 1 job mode is the future sitemap-first discovery job. Control Center
and ``python cli.py stage1 job-mode`` print these status strings. This module
does not start rustmapper, enqueue seeds, or choose a failure policy (#587).
"""

from __future__ import annotations

from typing import TypedDict


class DegradedMode(TypedDict):
    status: str
    label: str
    when: str
    next_action: str


class DegradedReason(TypedDict):
    status: str
    label: str
    mode: str
    next_action: str


INTRO = (
    "Stage 1 job mode (sitemap-first) prints one mode status and one reason "
    "status when rustmapper discovery degrades. The same words are used in "
    "Control Center and the CLI. Choosing the mode at runtime remains issue "
    "587; this is the operator copy only."
)

RUNBOOK = "Scraping_project/docs/runbooks/rustmapper_discovery_degraded.md"
JOB_MODE_DOC = "Scraping_project/src/stage1/JOB_MODE.md"

MODES: tuple[DegradedMode, ...] = (
    {
        "status": "abort",
        "label": "Abort",
        "when": (
            "The Stage 1 job stops. An empty or crashed rustmapper run is not "
            "a successful discovery and must not seed Stage 2."
        ),
        "next_action": (
            "Leave the job failed. Read the reason string on the job. Do not "
            "resume, and do not start Python sitemap discovery, unless you "
            "switch this job to fallback_python."
        ),
    },
    {
        "status": "fallback_python",
        "label": "Fallback Python",
        "when": (
            "Rustmapper is not the seed source for this job. Stage 1 continues "
            "with SitemapParser or Scout."
        ),
        "next_action": (
            "Confirm the handoff discovery_source is sitemap_parser or scout. "
            "A missing rustmapper sitemap.jsonl is not zero Python URLs. If "
            "Python is also empty, that is an empty handoff (ack or skip "
            "Stage 2), not a rustmapper success."
        ),
    },
    {
        "status": "resume_data_dir",
        "label": "Resume data-dir",
        "when": (
            "The same rustmapper data-dir (WAL and sitemap.jsonl) should "
            "continue. A new directory is a different crawl."
        ),
        "next_action": (
            "Run rustmapper resume --data-dir <that dir> (from source: "
            "cargo run --release -- resume --data-dir <dir>). Use the job "
            "existing data-dir. Do not delete the WAL. Enqueue only after the "
            "handoff status is success."
        ),
    },
)

REASONS: tuple[DegradedReason, ...] = (
    {
        "status": "install_missing",
        "label": "Install missing",
        "mode": "abort",
        "next_action": (
            "Install rustmapper, or switch the job to fallback_python. Do not "
            "resume a data-dir that was never created."
        ),
    },
    {
        "status": "crawl_empty",
        "label": "Empty frontier",
        "mode": "abort",
        "next_action": (
            "Do not enqueue. Empty discovery is not success. If sitemap.jsonl "
            "has URLs, the job is not empty; check the handoff. Otherwise fix "
            "seeds or switch to fallback_python."
        ),
    },
    {
        "status": "timeout_storm",
        "label": "Timeout storm",
        "mode": "resume_data_dir",
        "next_action": (
            "Prefer sitemap seeding and a shorter timeout (--timeout 5, "
            "--seeding-strategy sitemap, per Rust-sitemap troubleshooting), "
            "then resume the same data-dir. Use fallback_python if this job "
            "cannot wait."
        ),
    },
    {
        "status": "oom",
        "label": "Out of memory",
        "mode": "resume_data_dir",
        "next_action": (
            "Lower workers (--workers 64 is the Rust-sitemap relief valve) and "
            "resume the same data-dir. Do not raise workers first."
        ),
    },
    {
        "status": "crash",
        "label": "Crash",
        "mode": "resume_data_dir",
        "next_action": (
            "Resume the same data-dir. If resume fails again, abort or switch "
            "to fallback_python. Do not start a second data-dir for this job."
        ),
    },
    {
        "status": "sitemap_jsonl_missing",
        "label": "sitemap.jsonl missing",
        "mode": "abort",
        "next_action": (
            "Abort. fallback_python only when this job mode allows Python "
            "discovery. resume_data_dir only when that data-dir already exists."
        ),
    },
)


def format_job_mode_text() -> str:
    """Plain-text copy for the CLI and Stage 1 job-mode help."""
    lines = [
        "Stage 1 job mode — rustmapper discovery degraded modes",
        "",
        INTRO,
        "",
        f"Runbook: {RUNBOOK}",
        f"Job mode: {JOB_MODE_DOC}",
        "",
        "Mode status strings",
    ]
    for mode in MODES:
        lines.append(f"- {mode['status']}: {mode['when']}")
        lines.append(f"  Next: {mode['next_action']}")
    lines.append("")
    lines.append("Reason status strings")
    for reason in REASONS:
        lines.append(f"- {reason['status']} -> {reason['mode']}: {reason['next_action']}")
    return "\n".join(lines)
