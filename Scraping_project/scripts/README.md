# Scraping Pipeline - Management Scripts

This directory contains scripts for managing, debugging, and resetting the scraping pipeline infrastructure.

## Quick Reference

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `diagnose_issues.sh` | Check system health and identify problems | First step when troubleshooting |
| `complete_reset.sh` | Full Docker stack reset and rebuild (guarded) | Major issues, fresh start needed |
| `reset_lake.py` | Move the Delta Lake aside and re-seed (guarded) | Start the lake over from seeds |
| `../drain_lake.py` | Empty pipeline queue tables, undoable (guarded) | Stuck or poisoned queues |
| `reset_grafana_complete.sh` | Reset Grafana only (Docker/K8s) | Grafana login or dashboard issues |
| `k8s_reset_and_deploy.sh` | Remove "coco" prefix and redeploy K8s | Fix Kubernetes naming issues |

---

## Destructive operations are guarded (#522, #576)

`complete_reset.sh`, `reset_lake.py`, `drain_lake.py` (and `cli.py drain`), plus
`make docker-down-clean` / `clean-all` / `db-reset`, all share one guard
(`src/utils/destructive_guard.py`):

| Rule | How |
|---|---|
| **Dry run by default** | Without `--execute` they only list what would be affected (tables with Delta version and row estimate, or volumes) and exit 0 |
| **Dual confirmation** | `--execute --i-really-mean-it` **and** `ALLOW_LAKE_RESET=1` in the environment. For make targets: `I_REALLY_MEAN_IT=1 ALLOW_LAKE_RESET=1 make <target>` |
| **Production break-glass** | With `ENV=prod`/`production` (the Helm default) also `--break-glass` (`BREAK_GLASS=1` for make) **and** typing `<operation> <env>` on an interactive terminal. There is no non-interactive way to wipe production |
| **Audit trail** | Every attempt (dry run, refusal, execution, failure) appends a JSON line (actor, sudo user, host, pid, argv, env, targets, outcome, Delta versions before) to `data/logs/destructive_ops.jsonl` (override: `LAKE_AUDIT_LOG`). It sits outside the lake, so a reset can't erase its own record |

Exit codes: `0` done or dry run, `3` refused (missing confirmation), `1` failed.

```bash
python scripts/reset_lake.py                       # dry run
ALLOW_LAKE_RESET=1 python scripts/reset_lake.py --execute --i-really-mean-it
python drain_lake.py --list
ALLOW_LAKE_RESET=1 python drain_lake.py --execute --i-really-mean-it      # transient queues
ALLOW_LAKE_RESET=1 ./scripts/complete_reset.sh --execute --i-really-mean-it
```

### Recovery

| Operation | What it actually does | Undo |
|---|---|---|
| `reset_lake.py --execute` | **Moves** the lake to `<lake>.bak-<UTC stamp>` (nothing copied or deleted), then re-seeds | `mv data/delta_lake data/delta_lake.failed && mv data/delta_lake.bak-<stamp> data/delta_lake` |
| `reset_lake.py --execute --no-backup` | Deletes the lake; additionally needs `DELTA_ALLOW_HARD_DELETE=1` | None. Restore from your volume/object-store backup |
| `reset_lake.py --seed-only --execute` | Overwrites `seed_urls` (Delta history kept) | Time travel: `DeltaTable(path).restore(<previous version>)` |
| `drain_lake.py --execute` | Delta DELETE of every row in the queue tables (schema and history kept). Prints the pre-drain version | `drain_lake.py --restore <table> --to-version <v> --execute --i-really-mean-it` (`<v>` is also in the audit log) |
| `complete_reset.sh` / `make docker-down-clean` | Removes Docker volumes, **including `delta_data` (the lake)** | None. Snapshot the volume first: `docker run --rm -v scraping_project_delta_data:/d -v "$PWD":/b alpine tar czf /b/delta_data.tgz -C /d .` |

The lake these tools act on is the one the pipeline writes: `DELTA_LAKE_PATH`, else
`delta_lake.base_path` from config.

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

**Usage** (dry run by default; see [Destructive operations](#destructive-operations-are-guarded-522-576)):
```bash
./scripts/complete_reset.sh               # guard dry run: nothing changed
./scripts/complete_reset.sh --dry-run     # show which services each step would start
ALLOW_LAKE_RESET=1 ./scripts/complete_reset.sh --execute --i-really-mean-it              # interactive
ALLOW_LAKE_RESET=1 ./scripts/complete_reset.sh --execute --i-really-mean-it --yes --no-rebuild
```

**Interactive prompts**:
- Confirmation before deleting data (after the guard)
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

# If Prometheus is down, full reset needed (removes all volumes, incl. the lake)
ALLOW_LAKE_RESET=1 ./scripts/complete_reset.sh --execute --i-really-mean-it
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

# If needed, full reset (removes all volumes, incl. the lake)
ALLOW_LAKE_RESET=1 ./scripts/complete_reset.sh --execute --i-really-mean-it
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

| Service | Port | URL |
|---------|------|-----|
| Grafana | 3000 | http://localhost:3000 |
| Prometheus A | 9091 | http://localhost:9091 |
| Prometheus B | 9097 | http://localhost:9097 |
| Alertmanager 1 | 9093 | http://localhost:9093 |
| Metrics Exporter | 9090 | http://localhost:9090/metrics |
| Redis | 6379 | redis://localhost:6379 |
| PostgreSQL | 5432 | postgres://localhost:5432 |
| Kafka | 9092 | kafka://localhost:9092 |
| Kafka External | 9094 | kafka://localhost:9094 |

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
# 1. Complete reset (removes all volumes, incl. the lake)
ALLOW_LAKE_RESET=1 ./scripts/complete_reset.sh --execute --i-really-mean-it

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

## Script Development

All scripts follow these conventions:
- Colored output (green=success, yellow=warning, red=error)
- Confirmation prompts for destructive operations
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
