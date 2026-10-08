# Guides

These guides cover the current Delta Lake pipeline. The old SQLite storage is gone, so ignore any SQLite instructions you find in older issues or notes.

| Guide | Covers |
|---|---|
| [Configuration](CONFIGURATION.md) | Every `config.yml` section, the environment variables that override it, and how code reads it |
| [Running](RUNNING.md) | Prerequisites, running the stages, crawling a different domain, resuming after a crash |
| [Monitoring](MONITORING.md) | Grafana dashboards, Prometheus metrics and alerts, logs, the Delta transaction log |
| [Data usage](DATA_USAGE.md) | Querying the Delta tables from Python and exporting to CSV, JSON lines or Parquet |

Run every command from `Scraping_project/`.
