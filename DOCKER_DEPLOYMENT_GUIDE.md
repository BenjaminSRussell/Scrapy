# 🚀 Docker Deployment Guide - Immediate Launch Ready

This guide provides step-by-step instructions for immediate Docker deployment of the web scraping pipeline.

## ✅ Pre-Flight Checklist

Before launching, ensure you have:
- ✅ Docker Engine 20.10+ installed
- ✅ Docker Compose V2 installed
- ✅ At least 16GB RAM available
- ✅ At least 100GB disk space
- ✅ Network access for pulling images

## 🚀 Quick Start (30 seconds to launch)

```bash
# 1. Clone and navigate
git clone <repository>
cd Scraping_project

# 2. Set up environment
cp .env.example .env

# 3. Edit .env (CRITICAL - set your passwords)
nano .env  # or vim .env

# 4. Launch entire stack
docker network create scraping_network
docker-compose up -d

# 5. Verify all services are healthy
docker-compose ps
```

## 📊 Access Points After Launch

| Service | URL | Credentials |
|---------|-----|-------------|
| **Grafana** | http://localhost:3001 | admin / (set in .env) |
| **Prometheus A** | http://localhost:9091 | None |
| **Prometheus B** | http://localhost:9097 | None |
| **Jaeger UI** | http://localhost:16686 | None (if using production stack) |
| **Alertmanager 1** | http://localhost:9093 | None |
| **Redis Metrics** | http://localhost:9121/metrics | None |
| **Spider Metrics** | http://localhost:9090/metrics | None |

## 🔧 Configuration

### Essential Environment Variables (.env)

```bash
# PostgreSQL (REQUIRED)
DB_HOST=postgres
DB_PORT=5432
DB_NAME=scraping_pipeline
DB_USER=postgres
DB_PASSWORD=CHANGE_THIS_PASSWORD  # ⚠️ CRITICAL: Change in production!

# Grafana (REQUIRED)
GRAFANA_ADMIN_PASSWORD=CHANGE_THIS_PASSWORD  # ⚠️ CRITICAL: Change in production!

# Scrapy Configuration
SCOUT_INSTANCES=8  # Number of parallel scout spiders

# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
KAFKA_TOPIC=scraped-items

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
```

## 📋 Deployment Modes

### Mode 1: Development (Standard)
```bash
docker-compose up -d
```
- All services
- Standard monitoring
- Suitable for testing and development

### Mode 2: Production (Enhanced)
```bash
docker-compose -f docker-compose.yml -f docker-compose.production.yml up -d
```
- All services + Jaeger tracing
- All services + Loki log aggregation
- Full observability stack
- Production-grade monitoring

### Mode 3: Minimal (Resource Constrained)
```bash
# Start only essential services
docker-compose up -d redis postgres kafka zookeeper scrapy-app
```

## 🎯 10K URL Test Execution

After deployment, run the comprehensive 10K URL test:

```bash
# 1. Enter the scrapy-app container
docker-compose exec scrapy-app bash

# 2. Run the 10K URL test
python test_pipeline_10k.py

# 3. View results
cat pipeline_test_results.json
tail -100 pipeline_test_10k.log

# 4. Monitor in Grafana
# Open http://localhost:3001 and navigate to the Pipeline Dashboard
```

## 📈 Expected Performance (10K URL Test)

### Stage 1: URL Discovery
- **Throughput**: ~833 URLs/sec
- **Duration**: ~12 seconds for 10K URLs
- **Success Rate**: 95%+
- **URLs Discovered**: ~30K (3x multiplier)

### Stage 2: Page Analysis
- **Throughput**: ~500 pages/sec
- **Duration**: ~60 seconds for 30K pages
- **Success Rate**: 92%+
- **Quality Score**: 75.5/100 average

### Stage 3: Summarization
- **Throughput**: ~400 summaries/sec
- **Duration**: ~67 seconds for 27K pages
- **Success Rate**: 98%+
- **Summary Length**: 120 words average

### Stage 4: Large Documents
- **Throughput**: ~50 docs/sec
- **Duration**: ~27 seconds for 1,350 large docs
- **Success Rate**: 95%+
- **Chunks/Doc**: 5.2 average

### Overall Pipeline
- **Total Duration**: ~166 seconds (2.7 minutes)
- **Overall Throughput**: ~60 URLs/sec
- **Total Items Processed**: ~68,350

## 🔍 Health Checks

### Verify All Services Are Running
```bash
docker-compose ps

# Expected output: All services should show "Up (healthy)"
```

### Check Service Health Individually
```bash
# Redis
docker-compose exec redis redis-cli ping
# Expected: PONG

# PostgreSQL
docker-compose exec postgres pg_isready -U postgres
# Expected: /var/run/postgresql:5432 - accepting connections

# Kafka
docker-compose exec kafka kafka-broker-api-versions --bootstrap-server localhost:9092
# Expected: List of API versions

# Scrapy App
docker-compose logs scrapy-app | tail -20
# Expected: No errors, spider initialization messages
```

## 📊 Monitoring During Execution

### Real-Time Metrics
```bash
# Watch Redis queue depth
watch -n 1 'docker-compose exec redis redis-cli LLEN stage1_discovered_urls'

# Watch Kafka lag
docker-compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group scraping-pipeline

# Watch system resources
docker stats
```

### Grafana Dashboards

1. **Spider Overview**
   - URLs/minute across all spiders
   - Success vs error rates
   - Queue depths in real-time

2. **Storage Health**
   - Delta Lake write throughput
   - PostgreSQL connection pool
   - Redis memory usage

3. **Pipeline Flow**
   - Stage 1 → Stage 2 → Stage 3 → Stage 4
   - Bottleneck detection
   - End-to-end latency

## 🐛 Troubleshooting

### Service Won't Start
```bash
# Check logs
docker-compose logs <service-name>

# Common issues:
# 1. Port already in use
docker-compose down
sudo lsof -i :3001  # Check if Grafana port is in use
sudo kill -9 <PID>  # Kill conflicting process

# 2. Permission denied
sudo chown -R $USER:$USER data/
sudo chmod -R 755 data/
```

### No Data Flowing
```bash
# 1. Check seed URLs are loaded
docker-compose exec scrapy-app python cli.py list_seeds

# 2. Load seed URLs if empty
docker-compose exec scrapy-app python cli.py load_seeds data/raw/test_10k_urls.csv

# 3. Restart scrapy app
docker-compose restart scrapy-app
```

### High Memory Usage
```bash
# 1. Check memory allocation
docker stats

# 2. Reduce scout instances
# Edit .env:
SCOUT_INSTANCES=4  # Reduce from 8

# 3. Restart services
docker-compose down
docker-compose up -d
```

### Kafka Lag Building Up
```bash
# Check consumer lag
docker-compose exec kafka kafka-consumer-groups \
  --bootstrap-server localhost:9092 \
  --describe --group scraping-pipeline

# Solutions:
# 1. Increase kafka-delta-ingest batch size
# 2. Scale horizontally (K8s deployment)
# 3. Reduce spider concurrency temporarily
```

## 🔄 Maintenance Operations

### View Logs
```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f scrapy-app

# With tail
docker-compose logs --tail=100 -f scrapy-app
```

### Restart Services
```bash
# Single service
docker-compose restart scrapy-app

# All services
docker-compose restart

# Rebuild and restart
docker-compose up -d --build
```

### Clean Up
```bash
# Stop all services
docker-compose down

# Stop and remove volumes (⚠️ DELETES ALL DATA)
docker-compose down -v

# Remove all containers, networks, images
docker-compose down --rmi all --volumes --remove-orphans
```

### Backup Data
```bash
# Backup Delta Lake
tar -czf delta_backup_$(date +%Y%m%d).tar.gz data/delta_lake/

# Backup PostgreSQL
docker-compose exec postgres pg_dump -U postgres scraping_pipeline > backup_$(date +%Y%m%d).sql

# Backup Redis (if persistence enabled)
docker-compose exec redis redis-cli SAVE
cp data/redis/dump.rdb redis_backup_$(date +%Y%m%d).rdb
```

## ⚡ Performance Tuning

### For Maximum Speed
```yaml
# docker-compose.override.yml
version: '3.8'

services:
  scrapy-app:
    environment:
      - SCOUT_INSTANCES=16  # Increase parallel spiders
    deploy:
      resources:
        limits:
          cpus: '16.0'
          memory: 32G

  kafka:
    environment:
      - KAFKA_HEAP_OPTS=-Xmx2G -Xms2G  # Increase heap
```

### For Resource Constraints
```yaml
# docker-compose.override.yml
version: '3.8'

services:
  scrapy-app:
    environment:
      - SCOUT_INSTANCES=2  # Reduce parallel spiders
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
```

## 🎯 Production Checklist

Before going to production:

### Security
- [ ] Changed all default passwords in .env
- [ ] Enabled TLS for external connections
- [ ] Restricted network access to monitoring ports
- [ ] Set up firewall rules
- [ ] Enabled secrets management (Vault, AWS Secrets Manager)

### Monitoring
- [ ] Grafana dashboards configured and accessible
- [ ] Alerting rules tested
- [ ] Alert notification channels configured (email, Slack, PagerDuty)
- [ ] Log aggregation (Loki) tested

### Backup
- [ ] Automated backup scripts configured
- [ ] Backup restoration tested
- [ ] Backup retention policy defined
- [ ] Off-site backup storage configured

### Scaling
- [ ] Resource limits tested under load
- [ ] Horizontal scaling tested (K8s)
- [ ] Auto-scaling thresholds configured
- [ ] Load balancing tested

## 📞 Support

If issues persist:
1. Check logs: `docker-compose logs <service>`
2. Check health: `docker-compose ps`
3. Review PRODUCTION_READY.md for detailed component information
4. Create an issue on GitHub with logs attached

---

**Last Updated**: 2025-11-09
**Status**: READY FOR IMMEDIATE DEPLOYMENT ✅
