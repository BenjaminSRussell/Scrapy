# 📚 Documentation Index

Complete guide to all documentation and resources for the UConn Scraping Pipeline.

---

## 🚀 Getting Started

### For New Users
1. **[QUICK_START.md](QUICK_START.md)** - One-command setup guide
   - Run `./start.sh` to get everything running
   - Lists all services and endpoints
   - Shows what `start.sh` does step-by-step

2. **[README.md](README.md)** - Complete pipeline overview
   - Architecture diagrams
   - Feature descriptions
   - Service endpoints table
   - All 10 Grafana dashboards explained
   - CLI commands reference

---

## 📊 Service Endpoints Reference

After running `./start.sh`, access these services:

| Service | URL | Login | Purpose |
|---------|-----|-------|---------|
| 🔴 Redis | `localhost:6379` | - | Message queues |
| 🐘 PostgreSQL | `localhost:5432` | postgres/postgres | Analytics DB |
| 📈 Prometheus | http://localhost:9090 | - | Metrics storage |
| 📊 Grafana | http://localhost:3000 | admin/admin | Dashboards |
| Redis Exporter | `localhost:9121` | - | Metrics export |
| Postgres Exporter | `localhost:9187` | - | Metrics export |

---

## 📊 Grafana Dashboards

### Main Pipeline Dashboard
Visit http://localhost:3000 (admin/admin) to see:

1. **Queue Depths** - Real-time message queue sizes
2. **System Throughput** - Processing rates per stage
3. **Error Rates** - 5-minute rolling error averages
4. **Consumer Lag** - Processing delay in seconds
5. **Circuit Breaker Status** - Blocked domain count
6. **Total URLs Discovered** - Cumulative URL count
7. **Active Workers** - Worker counts by stage
8. **Redis Memory Usage** - Memory consumption
9. **Error Breakdown** - Error type distribution (pie chart)
10. **Domain Distribution** - Top 10 crawled domains

### Built-in Dashboards
- **Redis Dashboard** - Clients, memory, commands, keyspace
- **PostgreSQL Dashboard** - Size, connections, queries

---

## 🎯 What `start.sh` Does

The automated startup script performs:

### [1/5] Prerequisites Check
- ✅ Python 3.10+
- ✅ Docker running
- ✅ Docker Compose installed

### [2/5] Python Environment
- ✅ **Creates `.venv` virtual environment**
- ✅ Activates it automatically
- ✅ Upgrades pip
- ✅ Installs all dependencies from `pyproject.toml`

### [3/5] Docker Infrastructure
- ✅ Starts 6 containers:
  - Redis, PostgreSQL, Prometheus, Grafana
  - Redis Exporter, PostgreSQL Exporter
- ✅ Waits for health checks

### [4/5] Verification
- ✅ Tests all connections
- ✅ Displays endpoints

### [5/5] Next Steps
- ✅ Shows queue status
- ✅ Provides run commands

---

## 📁 Technical Documentation

### Implementation Details
- **[IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)**
  - All 13 tasks completed
  - 17 new files created (6,100+ lines)
  - Architecture improvements
  - Performance enhancements

### Configuration
- **[config.yml](config.yml)**
  - Redis settings
  - Stage 1-4 configurations
  - Message queue mappings
  - Monitoring settings

### Infrastructure
- **[docker-compose.yml](docker-compose.yml)**
  - All service definitions
  - Health checks
  - Volume mappings
  - Network configuration

---

## 🛠️ Tools & Utilities

### Queue Management
```bash
python drain_lake.py --list              # List all queues
python drain_lake.py --drain-transient   # Clear transient queues
python drain_lake.py --queue <name>      # Drain specific queue
```

### Pipeline Management
```bash
python run_pipeline.py health            # Check system health
python run_pipeline.py export --all      # Export all data
python run_pipeline.py reset             # Reset pipeline
```

### Monitoring
```bash
python monitoring/metrics_exporter.py    # Start metrics exporter
docker-compose logs -f                   # View all logs
docker-compose logs -f grafana           # View Grafana logs
```

---

## 🏗️ Architecture Components

### Core Infrastructure (in `src/common/`)
- **redis_manager.py** - URL deduplication, priority queue, circuit breaker
- **config.py** - Configuration loader from config.yml
- **delta_lake.py** - ACID storage manager
- **postgres_manager.py** - Analytics database
- **retry_middleware.py** - Exponential backoff, circuit breaker middleware

### Stage 1 Enhancements (in `src/stage1/`)
- **scout_spider.py** - Main Scrapy spider
- **sitemap_parser.py** - Recursive sitemap discovery
- **js_detection.py** - Advanced SPA framework detection
- **ultra_discovery.py** - 20+ URL extraction methods

### Consumers (in `src/consumers/`)
- **delta_consumer.py** - Standalone Delta Lake writer

### Monitoring (in `monitoring/`)
- **metrics_exporter.py** - Custom pipeline metrics
- **grafana_dashboard.json** - Dashboard definition
- **prometheus.yml** - Metrics collection config

---

## 🔄 Data Flow

### Monolithic Mode
```
Scrapy Spider → Delta Lake (direct)
```

### Distributed Mode
```
Scrapy Spider → Redis Queues → Delta Consumer → Delta Lake
                    ↓
              Metrics Exporter → Prometheus → Grafana
```

---

## 📖 Quick Reference

### Start Everything
```bash
./start.sh
```

### Run Pipeline (Monolithic)
```bash
python run_pipeline.py run
```

### Run Pipeline (Distributed)
```bash
# Terminal 1
python monitoring/metrics_exporter.py

# Terminal 2
python src/consumers/delta_consumer.py --all

# Terminal 3
cd src/stage1 && scrapy crawl scout

# Terminal 4 (optional)
python src/stage2/stage2_worker.py --workers 100
```

### Stop Everything
```bash
docker-compose down
deactivate  # Exit virtual environment
```

---

## 🆘 Troubleshooting

### Common Issues

**Services won't start:**
```bash
docker info          # Check Docker running
docker-compose logs  # View error logs
```

**Redis connection failed:**
```bash
redis-cli ping              # Test connection
docker-compose restart redis # Restart service
```

**Queue issues:**
```bash
python drain_lake.py --list              # Check status
python drain_lake.py --drain-transient   # Clear queues
```

**Virtual environment errors:**
```bash
source .venv/bin/activate  # Activate venv
pip install -e .           # Reinstall deps
```

---

## 📚 Additional Resources

### GitHub
- [Issues](https://github.com/benjaminrussell/uconn-scraper/issues) - Report bugs
- [Discussions](https://github.com/benjaminrussell/uconn-scraper/discussions) - Ask questions

### External Documentation
- [Scrapy](https://docs.scrapy.org/) - Web scraping framework
- [Delta Lake](https://delta.io/) - ACID storage
- [Grafana](https://grafana.com/docs/) - Dashboards
- [Prometheus](https://prometheus.io/docs/) - Metrics
- [Redis](https://redis.io/documentation) - Message queues

---

## 🎓 Learning Path

### Beginner
1. Read [QUICK_START.md](QUICK_START.md)
2. Run `./start.sh`
3. Try monolithic mode: `python run_pipeline.py run`
4. View Grafana at http://localhost:3000

### Intermediate
1. Read [README.md](README.md) architecture section
2. Try distributed mode (4 terminals)
3. Explore `config.yml` settings
4. Use `drain_lake.py` for queue management

### Advanced
1. Read [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)
2. Study `src/common/redis_manager.py`
3. Customize Grafana dashboards
4. Modify `config.yml` for performance tuning

---

<div align="center">

**🎉 Complete Documentation Package 🎉**

Everything you need to run, monitor, and maintain the pipeline!

[🚀 Quick Start](QUICK_START.md) • [📖 README](README.md) • [🛠️ Config](config.yml)

</div>
