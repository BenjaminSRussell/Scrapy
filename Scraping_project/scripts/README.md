# Scraping Pipeline - Management Scripts

This directory contains scripts for managing, debugging, and resetting the scraping pipeline infrastructure.

## Quick Reference

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `diagnose_issues.sh` | Check system health and identify problems | First step when troubleshooting |
| `complete_reset.sh` | Full Docker stack reset and rebuild | Major issues, fresh start needed |
| `reset_grafana_complete.sh` | Reset Grafana only (Docker/K8s) | Grafana login or dashboard issues |
| `k8s_reset_and_deploy.sh` | Remove "coco" prefix and redeploy K8s | Fix Kubernetes naming issues |
| `reset_lake.py` | Wipe the Delta lake and re-seed `seed_urls` | Local dev only; guarded (see below) |
| `reseed.py` | Bulk-load a CSV into `seed_urls` | `python scripts/reseed.py --csv …` |
| `drain_lake.py` | Selective Redis queue drain | `python scripts/drain_lake.py --list` |
| `audit_file_counts.py` | Count Python files per directory | One-shot layout audit |
| `monitor_and_trigger_depth.py` | Launch the depth spider when queues are idle | Long-running; needs Redis |
| `automated_backup.sh` | Dry-run archive of a lake directory and an optional SQL file | `make backup`. Does not run `pg_dump` or start services |
| `restore_backup.sh` | Dry-run restore, or a `/tmp` sample round-trip | `make restore`. `ROUNDTRIP=1` checks a sample. SQL is not loaded |
| `anomaly_scan.py` | Offline z-score of a metrics fixture | `make anomaly-scan`. Prints an Alertmanager payload and does not POST |

> **Destructive operations** (`complete_reset.sh`, `reset_lake.py`, `../cli.py reset`,
> `reseed.py --clear`, `drain_lake.py` drains) follow one safety policy; see
> [Destructive operations: safety policy](#destructive-operations-safety-policy).

## Backup and restore (#300)

`make backup` and `make restore` default to a dry-run. A dry-run does not read `data/delta_lake`, does not write archives, and does not run `pg_dump`, `psql`, Docker, Postgres, Redis, or Kafka.

Set `DRY_RUN=0` to write. `LAKE_SRC` and `BACKUP_DIR` are required and must be directories under `/tmp` or this worktree. `data/delta_lake` and any other path are refused. `RESTORE_DEST` has the same rule. `POSTGRES_DUMP_FILE`, when set, must already be a `.sql` file in one of those places. The scripts never create a dump and never load one.

Retention is applied by `automated_backup.sh` only. A dry-run lists what would be removed and deletes nothing.

| Variable | Default | Meaning |
|---|---|---|
| `RETENTION_DAYS` | `7` | Delete `delta_lake_*.tar.gz` and `postgres_*.sql` in `BACKUP_DIR` older than this many days. `0` disables the age cutoff. |
| `RETENTION_COUNT` | `0` | Keep at most this many newest `delta_lake_*.tar.gz` archives, plus the `postgres_*.sql` with the same id. `0` means no count cap. |

`ROUNDTRIP=1 ./scripts/restore_backup.sh` builds a sample lake and SQL file under `/tmp`, backs them up, restores them, and compares the bytes. It does not use the project lake.

## Anomaly scan (#501)

`make anomaly-scan` scores one local metrics fixture and exits. It does not read `data/delta_lake`, does not train on the live lake, does not install packages, and does not start Docker, Prometheus, or Alertmanager.

The score is a sample z-score of the latest point against the earlier points. A score strictly above `threshold` is an anomaly and the target exits non-zero. The default fixture is clean and exits 0.

```bash
make anomaly-scan
make anomaly-scan FIXTURE=scripts/fixtures/anomaly/clean_metrics.json
make anomaly-scan FIXTURE=scripts/fixtures/anomaly/anomaly_metrics.json
```

`scripts/kmeans_anomaly_detector.py` is loaded only when `numpy`, `scikit-learn`, `pandas`, and `duckdb` already import. If any of those are missing, the stdlib checker still decides the exit code. This target does not run that script's training path and does not run `ml_error_analyzer.py`.

### Input metrics

JSON object, one series:

| Field | Meaning |
|---|---|
| `metric` | Name copied into the stub alert. Default `metric`. |
| `threshold` | Z-score limit. The latest point exits 1 when its score is strictly above this. Default `3`. |
| `points` | Time-ordered objects `{"timestamp", "value"}`. At least three. Every point except the last is the baseline. |

Fixtures live in `scripts/fixtures/anomaly/clean_metrics.json` and `scripts/fixtures/anomaly/anomaly_metrics.json`.

### Alertmanager stub

When the threshold is crossed, the target prints an Alertmanager webhook payload and does not POST it. There is no HTTP client. `ALERTMANAGER_URL`, if set, is not called.

## Lake ops (`scrapy-ops`, #1098)

From `Scraping_project/`:

```bash
python scripts/ops/scrapy-ops lake compact
python scripts/ops/scrapy-ops lake vacuum          # preview; add --apply to delete
python scripts/ops/scrapy-ops lake doctor          # Redis ping, Kafka topics, Delta/_delta_log
```

`doctor` uses `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD` and `REDIS_DB`. It fails when a table directory has data files but no `_delta_log`. `temp_scripts/` is retired; CI rejects new files there.

## Deprecated root paths (#568)

`cli.py` is the supported Python entrypoint in the project root. These four utilities moved here. The old paths are deprecated redirects:

| Deprecated (do not use) | Run instead (from `Scraping_project/`) |
|---|---|
| `python reseed.py` | `python scripts/reseed.py` |
| `python drain_lake.py` | `python scripts/drain_lake.py` |
| `python audit_file_counts.py` | `python scripts/audit_file_counts.py` |
| `python monitor_and_trigger_depth.py` | `python scripts/monitor_and_trigger_depth.py` |

---

## Script Details

### 1. diagnose_issues.sh

**Purpose**: Comprehensive diagnostic tool to identify issues in your deployment.

**Features**:
- Detects environment (Docker Compose or Kubernetes)
- Checks service health and connectivity
- Validates configuration files
- Tests HTTP endpoints
- Scans logs for errors
- Identifies naming issues in Kubernetes

**Usage**:
```bash
./scripts/diagnose_issues.sh
```

**When to use**:
- ✅ First step when troubleshooting
- ✅ Before opening support tickets
- ✅ After deployment to verify everything works
- ✅ Periodic health checks

**Example output**:
```
=== Docker Services Status ===
✓ redis: Running and Healthy
✓ postgres: Running and Healthy
⚠ grafana: Running but not healthy
✗ kafka: Not running
```

---

### 2. complete_reset.sh

**Purpose**: Complete Docker Compose stack reset and rebuild.

**What it does**:
1. Stops all services and removes this Compose project's volumes (`docker compose down -v`)
2. Optionally rebuilds Docker images
3. Checks `.env` (warns about missing `GRAFANA_ADMIN_PASSWORD`/`DB_PASSWORD`, prints key names only, never values)
4. Starts services in dependency order: infrastructure, monitoring, exporters, applications
5. Verifies health and connectivity

Only services defined in the active Compose file are started. Kafka, Alertmanager and the
exporters belong to the full-stack compose (see #145); when they are absent they are listed
as skipped instead of failing `docker compose up`. `diagnose_issues.sh` and `../diagnose.sh`
use the same discovery (`scripts/compose_lib.sh`, i.e. `docker compose config --services`).

**Usage**:
```bash
./scripts/complete_reset.sh               # no-op: prints the plan, exits 2
./scripts/complete_reset.sh --dry-run     # show which services each step would start (exit 0)
./scripts/complete_reset.sh --confirm     # type 'yes' to delete all project volumes
ALLOW_LAKE_RESET=1 ./scripts/complete_reset.sh --confirm --yes --no-rebuild   # automation
```

**Interactive prompts**:
- Typed confirmation before deleting data (`yes`, or `production` with break-glass)
- Option to rebuild Docker images

**When to use**:
- ✅ Fresh start needed
- ✅ Corrupted volumes or data
- ✅ Major configuration changes
- ✅ After updating docker-compose.yml
- ✅ Grafana persistent issues

**Warning**: ⚠️ This deletes ALL data including:
- Scraped data in Delta Lake
- Prometheus metrics
- Grafana dashboards (if not provisioned)
- Kafka topics and messages
- PostgreSQL database

**Time**: ~5-10 minutes (depending on rebuild)

---

### 3. reset_grafana_complete.sh

**Purpose**: Reset Grafana only without affecting other services.

**What it does**:
1. Stops Grafana container/pod
2. Removes Grafana volume/PVC
3. Resets credentials to admin/admin
4. Restarts Grafana with fresh state
5. Verifies accessibility

**Supports**: Both Docker Compose and Kubernetes

**Usage**:
```bash
# Docker Compose
./scripts/reset_grafana_complete.sh

# Kubernetes (auto-detected)
./scripts/reset_grafana_complete.sh
```

**When to use**:
- ✅ Forgot Grafana password
- ✅ Grafana UI not loading
- ✅ Dashboard configuration issues
- ✅ Datasource connection problems
- ✅ "Invalid credentials" errors

**Preserves**:
- All other services and data
- Prometheus metrics
- Scraped data

**After reset**:
- Username: `admin`
- Password: `admin`
- Access: http://localhost:3000

**Time**: ~30 seconds

---

### 4. k8s_reset_and_deploy.sh

**Purpose**: Clean up "coco" prefix and redeploy with standardized naming.

**What it does**:
1. Uninstalls old "coco" Helm release
2. Removes all associated resources (PVCs, ConfigMaps, Secrets)
3. Creates fresh secrets with admin/admin credentials
4. Validates Helm chart
5. Deploys with standardized "scraping-pipeline-*" naming
6. Waits for pods to be ready
7. Provides access instructions

**Before (problematic)**:
```
coco-scraping-pipeline-grafana
coco-scraping-pipeline-prometheus
coco-scraping-pipeline-kafka
```

**After (standardized)**:
```
scraping-pipeline-grafana
scraping-pipeline-prometheus
scraping-pipeline-kafka
```

**Usage**:
```bash
./scripts/k8s_reset_and_deploy.sh
```

**Prerequisites**:
- kubectl installed and configured
- helm installed
- Access to Kubernetes cluster

**When to use**:
- ✅ "coco" prefix in service names
- ✅ Kubernetes deployment naming issues
- ✅ After cloning repository
- ✅ Clean Kubernetes deployment needed

**Warning**: ⚠️ Deletes ALL Kubernetes resources

**Time**: ~5-10 minutes

**After deployment**:
```bash
# Access Grafana
kubectl port-forward svc/scraping-pipeline-grafana 3000:3000

# Access Prometheus
kubectl port-forward svc/scraping-pipeline-prometheus 9090:9100
```

---

## Common Issues and Solutions

### Issue: "Cannot login to Grafana"
**Solution**:
```bash
./scripts/reset_grafana_complete.sh
```
Then login with `admin/admin`

---

### Issue: "Grafana shows 'Bad Gateway' or datasources not working"
**Solution**:
```bash
# Check what's wrong first
./scripts/diagnose_issues.sh

# If Prometheus is down, full reset needed
./scripts/complete_reset.sh --confirm
```

---

### Issue: "Services have 'coco-scraping-pipeline-*' names in Kubernetes"
**Solution**:
```bash
./scripts/k8s_reset_and_deploy.sh
```

---

### Issue: "Stage 4 worker not running"
**Check**: Stage 4 was added to docker-compose.yml. If missing:
```bash
# It should be there now, but if not:
docker-compose pull
docker-compose up -d stage4-worker
```

---

### Issue: "Kafka not connecting"
**Solution**:
```bash
# Check diagnostics first
./scripts/diagnose_issues.sh

# Look for Kafka errors
docker-compose logs kafka

# If needed, full reset
./scripts/complete_reset.sh --confirm
```

---

### Issue: "Pipeline stages not processing data"
**Debugging**:
```bash
# Check all stages
docker-compose logs -f scrapy-app
docker-compose logs -f stage2-worker
docker-compose logs -f stage3-worker
docker-compose logs -f stage4-worker

# Check queue depths in Redis
docker-compose exec redis redis-cli
> LLEN stage2_queue
> LLEN stage3_queue
> LLEN stage4_queue
```

---

## Environment Variables

### Required in `.env`:

```bash
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=scraping_pipeline
DB_USER=postgres
DB_PASSWORD=postgres

# Grafana (for Docker Compose)
GRAFANA_ADMIN_PASSWORD=admin
```

---

## Service Ports Reference

### Docker Compose:

Published by `docker-compose.yml` (the Kafka, Alertmanager and
metrics-exporter services run only in the Helm chart):

| Service | Port | URL |
|---------|------|-----|
| Grafana | 3000 | http://localhost:3000 |
| Prometheus | 9090 | http://localhost:9090 (targets: http://localhost:9090/targets) |
| Redis | 6379 | `localhost:6379` (`REDIS_HOST`/`REDIS_PORT`/`REDIS_PASSWORD`, see README "Redis connection contract") |
| PostgreSQL | 5432 | postgres://localhost:5432 |

`redis-exporter` (9121) and `postgres-exporter` (9187) are reachable only
inside the compose network, where Prometheus scrapes them.

### Kubernetes:

Use port-forwarding:
```bash
kubectl port-forward svc/<service-name> <local-port>:<service-port>
```

---

## Monitoring and Logs

### Docker Compose:

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f grafana

# Last 100 lines
docker-compose logs --tail=100 grafana

# Follow multiple services
docker-compose logs -f grafana prometheus-a
```

### Kubernetes:

```bash
# All pods
kubectl get pods

# Specific pod logs
kubectl logs -f <pod-name>

# Previous pod logs (if crashed)
kubectl logs --previous <pod-name>

# All pods with label
kubectl logs -l app.kubernetes.io/component=grafana -f
```

---

## Maintenance Best Practices

1. **Regular Diagnostics**: Run `diagnose_issues.sh` weekly
2. **Volume Cleanup**: Monitor disk usage, old volumes can accumulate
3. **Log Rotation**: Check log sizes periodically
4. **Backup**: Before major changes, backup:
   - `.env` file
   - `monitoring/` configs
   - Grafana dashboards (export from UI)
5. **Updates**: Keep Docker images updated:
   ```bash
   docker-compose pull
   docker-compose up -d
   ```

---

## Troubleshooting Checklist

Before asking for help, try:

1. ✅ Run diagnostic script: `./scripts/diagnose_issues.sh`
2. ✅ Check service logs: `docker-compose logs <service>`
3. ✅ Verify `.env` file exists and is correct
4. ✅ Ensure credentials are admin/admin
5. ✅ Try Grafana reset: `./scripts/reset_grafana_complete.sh`
6. ✅ Check disk space: `df -h`
7. ✅ Verify network connectivity: `docker network ls`

If still stuck:
- Collect output from diagnostic script
- Copy relevant logs
- Note what you've tried
- Describe expected vs actual behavior

---

## Quick Start After Reset

### Docker Compose:
```bash
# 1. Complete reset
./scripts/complete_reset.sh --confirm

# 2. Access Grafana
open http://localhost:3000

# 3. Login with admin/admin

# 4. Verify datasources (should be auto-configured)
# 5. Check dashboards are loading
```

### Kubernetes:
```bash
# 1. Reset and deploy
./scripts/k8s_reset_and_deploy.sh

# 2. Port forward Grafana
kubectl port-forward svc/scraping-pipeline-grafana 3000:3000

# 3. Access in browser
open http://localhost:3000

# 4. Login with admin/admin
```

---

## Destructive operations: safety policy

Applies to `scripts/complete_reset.sh`, `scripts/reset_lake.py`, `cli.py reset`,
`scripts/reseed.py --clear` and the drain modes of `scripts/drain_lake.py` (#522, #573, #576).
`python reseed.py` and `python drain_lake.py` at the project root are deprecated.
Implementation: `src/utils/destructive_guard.py` (the shell script mirrors it).

| Step | Rule |
|---|---|
| Default | **Dry-run.** Without `--confirm` nothing is touched: the plan is printed (Delta tables with row/byte estimates from the Delta log, Redis queues with sizes, or Compose services) and the command exits **2**. An explicit `--dry-run` exits 0. |
| Confirmation | `--confirm` **and** typing `yes`. For automation, `--yes` replaces the prompt only when `ALLOW_LAKE_RESET=1` is set. With no terminal and no `--yes`, the command refuses (exit **3**). |
| Production | When `ENV` (or `APP_ENV`) is `production`/`prod`: refused unless `--i-know-what-im-doing` **and** `ALLOW_LAKE_RESET=1`, and the operator must type `production`. `--yes` is ignored in production. |
| Audit | Every decision (`dry_run`, `refused`, `authorized`, `completed`, `failed`) is appended to `data/logs/destructive_ops.jsonl` (override: `DESTRUCTIVE_AUDIT_LOG`) with UTC time, actor (`SUDO_USER`/`USER`), host, env, argv and targets. |
| Legacy `--force` | Means `--confirm --yes`, so it still needs `ALLOW_LAKE_RESET=1`. |

```bash
python scripts/reset_lake.py                                  # plan only
python scripts/reset_lake.py --confirm --backup-dir /backups  # snapshot, then wipe + re-seed
python cli.py reset --confirm
python scripts/reseed.py --clear --confirm
python scripts/drain_lake.py --drain-transient                        # plan only
python scripts/drain_lake.py --drain-transient --confirm
ENV=production ALLOW_LAKE_RESET=1 python scripts/drain_lake.py --drain-all --confirm --i-know-what-im-doing
```

**Recovery.** A wiped lake directory cannot be restored with Delta time travel. Before
wiping anything you may need again, pass `--backup-dir DIR`: the lake is copied to
`DIR/<lake>-<UTC timestamp>` first, and the path is recorded in the audit log. To restore,
stop the workers, move the copy back to `data/delta_lake`, and restart. To roll back a bad
*write* (not a wipe), use time travel on the table instead (`DeltaTable(path).restore(version)`)
before `lake-vacuum --apply` removes the old files. `complete_reset.sh` deletes Docker volumes,
so snapshot them first (e.g. `docker run --rm -v <volume>:/v -v "$PWD":/b alpine tar czf /b/<volume>.tgz -C /v .`).

The Helm preStop hooks call `LakeDrainer().drain_transient_queues()` as a library and are not
gated. They import it from `scripts/drain_lake.py` (`sys.path` includes `/app/scripts`). Before
the guard landed they were a silent no-op: `drain_lake.py` imported the removed
`src.common.config`, and the hook's `|| true` swallowed the ImportError.

---

## Script Development

All scripts follow these conventions:
- Colored output (green=success, yellow=warning, red=error)
- Destructive operations: dry-run default, `--confirm` + typed confirmation, production break-glass, audit log
- Detailed progress messages
- Error handling with `set -e`
- Environment detection (Docker vs Kubernetes)
- Health checks after operations

---

## Support

For issues or questions:
1. Check this README
2. Run diagnostic script
3. Review logs
4. Check main project README
5. Open GitHub issue with diagnostic output

---

## Version Info

- Scripts version: 1.0.0
- Docker Compose file: latest
- Helm chart: 1.0.0
- Last updated: 2025-10-13

---

**Remember**: Always run diagnostics first! 🔍
