# Deployment Guide

Production deployment guide for the UConn Scraping Pipeline.

## Prerequisites

- Docker 20.10+
- Docker Compose 2.0+
- Kubernetes 1.24+ (for K8s deployment)
- Python 3.11+

## Quick Start with Docker Compose

```bash
# Build and start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Check service status
docker-compose ps

# Stop all services
docker-compose down
```

## Kubernetes Deployment

### 1. Build and Push Image

```bash
docker build -t your-registry/uconn-scraper:latest .
docker push your-registry/uconn-scraper:latest
```

### 2. Deploy to Kubernetes

```bash
# Minimal quick-start (workers + Redis). The supported full install is the
# Helm chart: see k8s/README.md.
kubectl apply -f k8s/deployment.yaml

# Check deployment status
kubectl get pods -n uconn-scraper
kubectl get services -n uconn-scraper

# View logs
kubectl logs -f deployment/stage1-worker -n uconn-scraper
```

### 3. Scale Workers

```bash
# Scale Stage 2 workers
kubectl scale deployment stage2-worker --replicas=5 -n uconn-scraper

# Auto-scaling
kubectl autoscale deployment stage2-worker \
  --cpu-percent=70 \
  --min=2 \
  --max=10 \
  -n uconn-scraper
```

## Configuration

### Environment Variables

- `REDIS_HOST` - Redis server hostname
- `REDIS_PORT` - Redis server port (default: 6379)
- `DELTA_LAKE_PATH` - Path to Delta Lake storage. Read by both `get_delta()` (DeltaHelper) and `LakehouseManager` (including the metrics exporter); it takes precedence over config.yml `delta_lake.base_path`. Compose/k8s use `/data/delta`, which compose mounts from host `./data/delta`. Local runs without the env var use `./data/delta_lake`.
- `LOG_LEVEL` - Logging level (DEBUG, INFO, WARNING, ERROR)
- `WORKERS` - Number of concurrent workers
- `CONCURRENCY` - Concurrency per worker

### Data Persistence

Mount volumes for persistent data:
- `/data` - Delta Lake tables
- `/app/logs` - Application logs

## Monitoring

### Prometheus + Grafana

```bash
# Access Grafana dashboard
http://localhost:3000
# Default credentials: admin/admin

# Access Prometheus
http://localhost:9090
```

### Health Checks

```bash
# Container health (Docker HEALTHCHECK = `python -m src.utils.probe container`:
# main process alive, Redis answers PING when REDIS_HOST is set, plus any
# HEALTHCHECK_TCP=host:port dependencies). Workers expose no HTTP /health route.
docker inspect --format '{{.State.Health.Status}}' <container>
docker compose exec stage3-worker python -m src.utils.probe container && echo healthy

# Check Redis
redis-cli ping
```

## CI/CD Pipeline

GitHub Actions workflow automatically:
1. Runs tests on PR and push
2. Builds Docker image on main branch
3. Deploys to Kubernetes on successful build

### Required Secrets

Configure in GitHub repository settings:
- `DOCKER_USERNAME` - Docker Hub username
- `DOCKER_PASSWORD` - Docker Hub password
- `KUBE_CONFIG` - Kubernetes config file

## Troubleshooting

### Worker Not Starting

```bash
# Check logs
docker-compose logs stage2-worker

# Restart service
docker-compose restart stage2-worker
```

### Redis Connection Issues

```bash
# Verify Redis is running
docker-compose ps redis

# Test connection
docker-compose exec redis redis-cli ping
```

### High Memory Usage

```bash
# Check resource usage
docker stats

# Adjust memory limits in docker-compose.yml
```

## Performance Tuning

### Redis Optimization

- Adjust `maxmemory` based on available RAM
- Use `allkeys-lru` eviction policy for caching
- Enable AOF persistence for durability

### Worker Scaling

- Stage 1: 1 instance (I/O bound)
- Stage 2: 2-5 instances (CPU bound)
- Stage 3: 1-2 instances (API limited)
- Stage 4: 1 instance (memory intensive)

### Resource Allocation

Recommended per worker:
- CPU: 0.5-1.0 cores
- Memory: 1-2 GB
- Storage: 100+ GB for Delta Lake

## Security

### Best Practices

1. Use non-root user in containers
2. Enable Redis password authentication
3. Use secrets management for credentials
4. Restrict network access with security groups
5. Enable TLS for external connections

### Secrets Management

```bash
# Create Kubernetes secrets
kubectl create secret generic redis-credentials \
  --from-literal=password=your-password \
  -n uconn-scraper
```

## Backup and Recovery

### Delta Lake Backup

```bash
# Backup Delta tables
rsync -av /data/delta/ /backup/delta/

# Restore from backup
rsync -av /backup/delta/ /data/delta/
```

### Redis Backup

```bash
# Enable AOF persistence
docker-compose exec redis redis-cli CONFIG SET appendonly yes

# Backup RDB file
docker cp uconn-redis:/data/dump.rdb ./backup/
```

## Maintenance

### Log Rotation

```bash
# Configure logrotate
/app/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
}
```

### Database Cleanup

```bash
# Compact Delta tables
python -m src.utils.maintenance compact

# Remove old logs
find /app/logs -name "*.log" -mtime +30 -delete
```

## Crawl artifact retention (`data gc`)

Long Kafka→Delta runs fill `data/logs`, `data/cache`, `data/temp`, and `logs`.
Use the CLI (also exposed as `scrapy-ops` when packaged):

```bash
# Dry-run: print candidate counts/bytes, delete nothing
python -m cli data gc --ttl-days 14 --verbose

# Apply: delete only files older than the TTL
python -m cli data gc --ttl-days 14 --apply
```

`--apply` is required to delete. Default roots: `data/logs`, `data/cache`,
`data/temp`, `logs`, `data/raw/tmp`.


## Seed registry CLI (#1100)

```bash
python -m cli seeds add https://example.com/page
python -m cli seeds list
python -m cli seeds disable https://example.com/page
python -m cli seeds audit
```

Registry: `data/ops/seeds.json`. Append-only audit: `data/ops/seed_audit.jsonl`.
Disable keeps the URL for history but removes it from active scheduling lists.
