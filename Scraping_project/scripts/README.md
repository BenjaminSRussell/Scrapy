# Scraping Pipeline - Management Scripts

This directory contains scripts for managing, debugging, and resetting the scraping pipeline infrastructure.

## Quick Reference

| Script | Purpose | When to Use |
|--------|---------|-------------|
| `diagnose_issues.sh` | Check system health and identify problems | First step when troubleshooting |
| `complete_reset.sh` | Full Docker stack reset and rebuild | Major issues, fresh start needed |
| `reset_grafana_complete.sh` | Reset Grafana only (Docker/K8s) | Grafana login or dashboard issues |
| `k8s_reset_and_deploy.sh` | Remove "coco" prefix and redeploy K8s | Fix Kubernetes naming issues |

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
1. Stops all services
2. Removes all volumes and data
3. Optionally rebuilds Docker images
4. Resets credentials to admin/admin
5. Starts services in correct order
6. Verifies health and connectivity

**Usage**:
```bash
./scripts/complete_reset.sh
```

**Interactive prompts**:
- Confirmation before deleting data
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
./scripts/complete_reset.sh
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
./scripts/complete_reset.sh
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
# 1. Complete reset
./scripts/complete_reset.sh

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
