# 🚀 Quick Start Guide

## One-Command Setup

```bash
./start.sh
```

That's it! This will:
- ✅ Check prerequisites
- ✅ Create virtual environment
- ✅ Install dependencies
- ✅ Start Docker services (Redis, PostgreSQL, Prometheus, Grafana)
- ✅ Verify everything is running

---

## What You Get

### 🌐 Services & Endpoints

| Service | URL/Port | Credentials | Purpose |
|---------|----------|-------------|---------|
| 🔴 **Redis** | `localhost:6379` | None | Message queues, URL deduplication, circuit breaker |
| 🐘 **PostgreSQL** | `localhost:5432` | user: `postgres`<br>pass: `postgres` | Analytics database, error logs, metrics |
| 📈 **Prometheus** | [http://localhost:9090](http://localhost:9090) | None | Metrics collection & time-series storage |
| 📊 **Grafana** | [http://localhost:3000](http://localhost:3000) | user: `admin`<br>pass: `admin` | Real-time dashboards |
| **Redis Exporter** | `localhost:9121` | None | Redis metrics → Prometheus |
| **PostgreSQL Exporter** | `localhost:9187` | None | PostgreSQL metrics → Prometheus |

### 📊 Grafana Dashboards Available

Visit [http://localhost:3000](http://localhost:3000) and login with `admin/admin` to access:

#### **Main Pipeline Dashboard** (Auto-provisioned)
1. **Queue Depths** - Real-time visualization of message queue sizes
   - Stage 1 → Stage 2 queue
   - Stage 2 → Stage 3 queue
   - Stage 2 → Stage 4 (Large Docs) queue
   - JS Render Queue

2. **System Throughput** - Processing rates per stage
   - URLs discovered/sec (Stage 1)
   - Pages analyzed/sec (Stage 2)
   - Summaries generated/sec (Stage 3)
   - Large docs processed/min (Stage 4)

3. **Error Rates by Stage** - 5-minute rolling average of errors

4. **Consumer Lag** - Processing delay in seconds for each consumer

5. **Circuit Breaker Status** - Number of currently blocked domains

6. **Total URLs Discovered** - Cumulative unique URL count

7. **Active Workers** - Worker count by stage

8. **Redis Memory Usage** - Current memory consumption in MB

9. **Error Breakdown** - Pie chart showing error type distribution

10. **Domain Request Distribution** - Top 10 most-crawled domains

#### **Built-in Dashboards**
- **Redis Dashboard** - Clients, memory trends, command statistics, keyspace
- **PostgreSQL Dashboard** - Database size, connections, query performance

---

## 🚀 What Does `start.sh` Do?

The `start.sh` script performs a complete automated setup:

### [1/5] Checking Prerequisites
- ✅ Python 3.10+
- ✅ Docker Desktop running
- ✅ Docker Compose installed

### [2/5] Python Environment Setup
- ✅ **Creates `.venv` virtual environment** (if doesn't exist)
- ✅ Activates the virtual environment
- ✅ Upgrades pip to latest version
- ✅ Installs all dependencies from `pyproject.toml`:
  - Scrapy, Playwright, httpx
  - Redis, psycopg2, prometheus-client
  - Transformers, torch
  - All other requirements

### [3/5] Docker Infrastructure
- ✅ Starts 6 Docker containers via `docker-compose up -d`:
  - Redis (message queues)
  - PostgreSQL (analytics)
  - Prometheus (metrics)
  - Grafana (dashboards)
  - Redis Exporter
  - PostgreSQL Exporter
- ✅ Waits for health checks to pass

### [4/5] Service Verification
- ✅ Displays all endpoint URLs and credentials
- ✅ Tests Redis connection

### [5/5] Status & Next Steps
- ✅ Shows current queue status
- ✅ Provides command examples for running the pipeline

---

## Running the Pipeline

### Option A: Monolithic Mode (Simpler)

```bash
python run_pipeline.py run
```

All stages run in one process. Data flows directly to Delta Lake.

### Option B: Distributed Mode (Production-Grade)

Open 3-4 terminals:

**Terminal 1: Metrics Exporter**
```bash
python monitoring/metrics_exporter.py
```

**Terminal 2: Delta Consumer**
```bash
python src/consumers/delta_consumer.py --all
```

**Terminal 3: Scrapy Spider**
```bash
cd src/stage1
scrapy crawl scout
```

**Terminal 4 (optional): Stage 2 Workers**
```bash
python src/stage2/stage2_worker.py --workers 100
```

Data flows: **Scrapy → Redis → Consumer → Delta Lake**

---

## Management Commands

### Check Health
```bash
python run_pipeline.py health
```

### Manage Queues
```bash
# List all queues
python drain_lake.py --list

# Drain transient queues (safe)
python drain_lake.py --drain-transient

# Drain specific queue
python drain_lake.py --queue stage1_discovered_urls
```

### Docker Commands
```bash
# View logs
docker-compose logs -f

# Restart services
docker-compose restart

# Stop services
docker-compose down

# Stop and remove volumes
docker-compose down -v
```

---

## Configuration

Edit `config.yml` to customize:

```yaml
redis:
  host: localhost
  port: 6379

stage1:
  concurrent_requests: 512
  circuit_breaker_enabled: true
  use_redis_queue: true

stage2:
  max_workers: 100
  retry_backoff_base: 2
```

---

## Monitoring

### Grafana Dashboard
1. Open http://localhost:3000
2. Login: admin/admin
3. View real-time metrics:
   - Queue depths
   - Processing throughput
   - Error rates
   - Consumer lag

### Prometheus
- http://localhost:9090
- Query metrics directly
- Example: `redis_queue_length{queue="stage1_discovered_urls"}`

---

## Troubleshooting

### Services Won't Start
```bash
# Check Docker is running
docker info

# View service logs
docker-compose logs redis
docker-compose logs grafana
```

### Redis Connection Failed
```bash
# Test connection
redis-cli ping

# Restart Redis
docker-compose restart redis
```

### Queue Management Issues
```bash
# Check queue status
python drain_lake.py --list

# Drain old queues
python drain_lake.py --drain-transient
```

---

## Next Steps

📚 **Documentation:**
- [README.md](README.md) - Full pipeline overview
- [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md) - What's new
- [config.yml](config.yml) - Configuration reference

🛠 **Tools:**
- `drain_lake.py` - Queue management utility
- `monitoring/metrics_exporter.py` - Custom metrics exporter

---

## Stopping Everything

```bash
# Stop Docker services
docker-compose down

# Deactivate virtual environment
deactivate
```

---

<div align="center">

**Need help?** Check the documentation or open an issue!

**Happy Scraping! 🎉**

</div>
