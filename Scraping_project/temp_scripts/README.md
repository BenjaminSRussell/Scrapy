# Scraping_project/temp_scripts/ (unsupported)

Scratch scripts from earlier phases (status checks, mock servers, ad-hoc tests). They
are **not** supported, are not run by CI (pytest only collects `tests/`), and many
target services, ports or modules that have since changed.

Supported equivalents:

| Instead of | Use |
|---|---|
| `START_PIPELINE.sh` / `STOP_PIPELINE.sh` | `python start.py` / `python shutdown.py` |
| `CHECK_PIPELINE_STATUS.sh` / `COMPLETE_SYSTEM_STATUS.sh` | `./diagnose.sh`, `scripts/diagnose_issues.sh`, `python cli.py health` |
| `run_orchestrator.py` / `run_individual_stage.py` | `python cli.py pipeline` (and its `--skip-*` flags) |
| `serve_dashboard.py` / `pipeline_dashboard.html` | `python dashboard/serve.py` |
| `*_test.py` / `test_*.py` here | real tests under `tests/` |
