# Runbook: rustmapper discovery degraded modes

Issue: #941. Adjacent runtime policy: #587 (not implemented here). No crawl.

Stage 1 job mode: [JOB_MODE.md](../../src/stage1/JOB_MODE.md).

Control Center: Pipeline Stages → Stage 1 (URL Discovery) → heading
**Stage 1 job mode**. CLI (prints this copy, does not crawl):

```bash
python cli.py stage1 job-mode
```

Rust-sitemap troubleshooting (`resume --data-dir`, timeout, out of memory,
empty frontier) is the source for the reason strings. An empty handoff is not
a successful discovery (`src/stage1/discovery_handoff.py`).

Stage 1 job mode (sitemap-first) prints one mode status and one reason status when rustmapper discovery degrades. The same words are used in Control Center and the CLI. Choosing the mode at runtime remains issue 587; this is the operator copy only.

## Mode status strings

### `abort`

The Stage 1 job stops. An empty or crashed rustmapper run is not a successful discovery and must not seed Stage 2.

Next: Leave the job failed. Read the reason string on the job. Do not resume, and do not start Python sitemap discovery, unless you switch this job to fallback_python.

### `fallback_python`

Rustmapper is not the seed source for this job. Stage 1 continues with SitemapParser or Scout.

Next: Confirm the handoff discovery_source is sitemap_parser or scout. A missing rustmapper sitemap.jsonl is not zero Python URLs. If Python is also empty, that is an empty handoff (ack or skip Stage 2), not a rustmapper success.

### `resume_data_dir`

The same rustmapper data-dir (WAL and sitemap.jsonl) should continue. A new directory is a different crawl.

Next: Run rustmapper resume --data-dir <that dir> (from source: cargo run --release -- resume --data-dir <dir>). Use the job existing data-dir. Do not delete the WAL. Enqueue only after the handoff status is success.

## Reason status strings

Each reason names the mode status to use.

### `install_missing`

Use mode `abort`.

Next: Install rustmapper, or switch the job to fallback_python. Do not resume a data-dir that was never created.

### `crawl_empty`

Use mode `abort`.

Next: Do not enqueue. Empty discovery is not success. If sitemap.jsonl has URLs, the job is not empty; check the handoff. Otherwise fix seeds or switch to fallback_python.

### `timeout_storm`

Use mode `resume_data_dir`.

Next: Prefer sitemap seeding and a shorter timeout (--timeout 5, --seeding-strategy sitemap, per Rust-sitemap troubleshooting), then resume the same data-dir. Use fallback_python if this job cannot wait.

### `oom`

Use mode `resume_data_dir`.

Next: Lower workers (--workers 64 is the Rust-sitemap relief valve) and resume the same data-dir. Do not raise workers first.

### `crash`

Use mode `resume_data_dir`.

Next: Resume the same data-dir. If resume fails again, abort or switch to fallback_python. Do not start a second data-dir for this job.

### `sitemap_jsonl_missing`

Use mode `abort`.

Next: Abort. fallback_python only when this job mode allows Python discovery. resume_data_dir only when that data-dir already exists.
