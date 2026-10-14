# temp_scripts/ (unsupported)

One-off operational experiments. They are **not** supported entry points, are not
tested by CI, and may reference services or paths that no longer exist.

Use the supported tooling instead (from `Scraping_project/`): `cli.py`, `start.py`,
`shutdown.py`, `diagnose.sh`, `scripts/`. Backup and restore are `make backup` and
`make restore` (`scripts/automated_backup.sh`, `scripts/restore_backup.sh`).
`BACKUP_S3_URI` can copy those archives to S3 or the Compose `backup` MinIO
profile. Credentials and restore are documented in `scripts/README.md`.
