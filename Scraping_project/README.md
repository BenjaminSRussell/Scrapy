# Scraping Pipeline with Kafka → Delta Lake → Prometheus

Production-ready web scraping pipeline with real-time metrics, Kafka streaming, and Delta Lake storage.

## 🚀 Quick Start

```bash
# 1. Start complete pipeline
./scripts/startup-pipeline.sh

# 2. Verify Kafka metrics
./scripts/verify-kafka-metrics.sh

# 3. Test end-to-end
./scripts/test-pipeline.sh

# 4. View Grafana dashboards
open http://localhost:3000  # admin / admin
```

## 📊 Architecture

```
Scrapy → Kafka → kafka-delta-ingest (Rust) → Delta Lake (MinIO/S3)
   │        │                │                       │
   │        │                │                       │
   ▼        ▼                ▼                       ▼
HTTP:9410  JMX:9999    StatsD:UDP         Parquet + _delta_log
   │        │                │                       │
   └────────┴────────────────┴───────────────────────┘
                          │
                          ▼
                   Prometheus:9091 (10s scrape)
                          │
                          ▼
                    Grafana:3000
```

## ✅ Features

- **Real-Time Metrics**: Kafka JMX → Prometheus (10s intervals)
- **Comprehensive Scrapy Tracking**: All signals (13 metrics total)
- **High-Performance Ingest**: Rust-based Kafka→Delta Lake
- **HA Monitoring**: Prometheus (2 replicas) + Alertmanager (3 nodes)
- **Multi-Stage Delta Lake**: Separate tables per pipeline stage

## 🔗 Service URLs

| Service | URL | Credentials |
|---------|-----|-------------|
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus A | http://localhost:9091 | - |
| Scrapy Metrics | http://localhost:9410/metrics | - |
| Kafka JMX | http://localhost:5556/metrics | - |

## 📦 Project Structure

```
├── cli.py                      # Unified CLI entrypoint for Scrapy + pipeline
├── src/
│   ├── pipelines.py           # Kafka pipeline with lifecycle management
│   ├── scrapy_prometheus.py   # All Scrapy metrics (13 signals)
│   ├── settings.py            # Environment-based config
│   └── common/
│       └── delta_lake.py      # Multi-stage Delta Lake manager
├── kafka-delta-ingest/        # Rust Kafka→Delta daemon
├── monitoring/                # All Prometheus/Grafana configs
├── scripts/                   # Operational scripts
└── docs → See below
```

## 📚 Documentation

### Quick References
- **[FINAL_SUMMARY.md](FINAL_SUMMARY.md)** - Implementation summary & metrics list
- **[KAFKA_REALTIME_SETUP.md](KAFKA_REALTIME_SETUP.md)** - Kafka→Prometheus setup

### Architecture & Design
- **[ADR-0001: Stage 1 Split](docs/adr/ADR-0001-stage1-layout.md)** - Decision record for splitting Stage 1 into Scout, JS Render, and Depth components.
- **[Stage 1 to Stage 2 Interface Contract](docs/contracts/stage1_to_stage2.md)** - Data contract for items passed from Stage 1 to Stage 2.
- **[JavaScript Rendering Policy](docs/policies/js_rendering.md)** - Policies for rendering JavaScript-heavy pages with Playwright.
- **[Metrics Catalog](docs/observability/metrics_catalog.md)** - Catalog of Prometheus metrics for monitoring.
- **[Testing Handbook](docs/testing/handbook.md)** - Guide to running and debugging tests.

### Runbooks & Ops
- **[ScoutSpider Runbook](docs/runbooks/scout_spider.md)** - Runbook for the `ScoutSpider`.
- **[DepthSpider Runbook](docs/runbooks/depth_spider.md)** - Runbook for the `DepthSpider`.
- **[Delta/Compose Troubleshooting](docs/ops/delta_troubleshooting.md)** - Troubleshooting guide for Delta Lake and Docker Compose.

### Legacy Guides
- **[DEPLOYMENT.md](DEPLOYMENT.md)** - Complete deployment guide
- **[OPERATIONS.md](OPERATIONS.md)** - Operational runbook
- **[IMPLEMENTATION_COMPLETE.md](IMPLEMENTATION_COMPLETE.md)** - Technical implementation notes

## 🛠️ CLI Usage

### Run Scrapy (Docker Entrypoint)
```bash
# Run default spider
python cli.py scrapy

# Run specific spiders
python cli.py scrapy --spiders scout discovery
```

### Run Full Pipeline
```bash
# Run all stages
python cli.py pipeline

# Skip stages
python cli.py pipeline --skip-stage1 --skip-stage2

# Adjust concurrency
python cli.py pipeline --stage2-workers 200 --stage3-workers 100
```

### Utility Commands
```bash
# Export Delta Lake tables
python cli.py export --table stage1_discovery --output exports --format csv
python cli.py export  # Export all tables

# Check health
python cli.py health

# Drain tables (keep seed URLs)
python cli.py drain
```

## 📊 Key Metrics

### Scrapy (http://localhost:9410/metrics)
- `scrapy_items_scraped_total` - Items/sec throughput
- `scrapy_responses_total{status_code}` - HTTP status tracking
- `scrapy_spider_errors_total{exception_type}` - Error categorization
- `scrapy_crawl_duration_seconds` - Total crawl time

### Kafka (http://localhost:5556/metrics)
- `kafka_server_brokertopicmetrics_messagesin_total` - Messages/sec
- `kafka_consumer_records_lag` - Consumer lag

### Delta Lake Ingest (http://localhost:9102/metrics)
- `kafka_delta_ingest_messages_received` - Consumption rate
- `kafka_delta_ingest_records_written` - Write rate

## 🔧 Configuration

### Environment Variables (.env)
```bash
# Kafka
KAFKA_BOOTSTRAP_SERVERS=kafka:9092
KAFKA_TOPIC=scraped-items

# Cloud Storage (MinIO local, or AWS S3 production)
AWS_ENDPOINT_URL=http://minio:9000      # Remove for real S3
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin123
AWS_REGION=us-east-1

# Database
POSTGRES_HOST=postgres
REDIS_HOST=redis
```

## 🎯 Production Deployment

### Switch to AWS S3
```bash
# 1. Update .env (remove AWS_ENDPOINT_URL, add real credentials)
# 2. Create S3 bucket with versioning enabled
# 3. Update docker-compose.yml table path to s3://your-bucket/path
```

See **[DEPLOYMENT.md](DEPLOYMENT.md)** for complete guide.

## 🐛 Troubleshooting

```bash
# Verify Kafka→Prometheus metrics flow
./scripts/verify-kafka-metrics.sh

# Check all services
docker-compose ps

# View logs
docker-compose logs -f kafka-delta-ingestor
```

See **[OPERATIONS.md](OPERATIONS.md)** for complete troubleshooting.

## 🏥 Development

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip-sync requirements.txt dev-requirements.txt

# Run tests
pytest

# Update dependencies
pip-compile requirements.in
pip-compile dev-requirements.in
```

## 📄 License

MIT

---

**Status: Production Ready** 🚀

All roadmap items completed. Real-time Kafka→Prometheus metrics working.
All Scrapy signals tracked. Delta Lake fully integrated.
