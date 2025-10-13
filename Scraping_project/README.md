# Scraping Pipeline with Kafka → Delta Lake → Prometheus

Production-ready web scraping pipeline with real-time metrics, Kafka streaming, and Delta Lake storage.

[![CI](https://github.com/benjaminrussell/Scraping_project/actions/workflows/main.yml/badge.svg)](https://github.com/benjaminrussell/Scraping_project/actions/workflows/main.yml)

## 🚀 Quick Start

1.  **Boot the local stack (no data reset):**
    ```bash
    python start.py
    ```

2.  **(Optional) Reset Delta Lake and reload seeds:**
    ```bash
    python start.py --reset-delta
    ```

3.  **Load additional seed URLs into the queue:**
    ```bash
    docker-compose exec scrapy-app python cli.py load_seeds
    ```

4.  **View the Grafana dashboards:**
    [http://localhost:3000](http://localhost:3000) (admin / admin)

## ⚙️ Lifecycle Management

*   **Start locally (docker-compose):** `python start.py`
*   **Stop locally:** `python shutdown.py`
*   **Stop locally and wipe project data:** `python shutdown.py --purge-data`
*   **Force a Delta Lake reset + reseed:** `python start.py --reset-delta`
*   **Start on Kubernetes (single release):**
    ```bash
    python start.py --env k8s --stage pipeline
    ```
*   **Start on Kubernetes (isolated stage clusters):**
    ```bash
    # Stage 1 / Scrapy discovery
    python start.py --env k8s --stage stage1

    # Stage 2 workers (disables Scrapy and Stage 3 in this release)
    python start.py --env k8s --stage stage2 --set stage2Worker.image.repository=ghcr.io/you/stage2 --set stage2Worker.image.tag=latest

    # Deploy all three stages into separate namespaces
    python start.py --env k8s --stage all-stages --release-prefix scraping --namespace-prefix scraping
    ```
    Each stage gets its own namespace (`<prefix>-stageX`) and Helm release (`<prefix>-stageX`). Use `--release`/`--namespace` when deploying a single stage to override the defaults.

*   **Stop a Kubernetes release:** `python shutdown.py --env k8s --release scraping-pipeline --namespace scraping`
*   **Stop a stage-specific release:** `python shutdown.py --env k8s --stage stage1` (adjust stage as needed)

For day-to-day troubleshooting you can still interact with Compose directly:

*   **Manual start:** `docker-compose up -d`
*   **Manual stop:** `docker-compose down`
*   **View logs:** `docker-compose logs -f`
*   **Run inside the Scrapy container:** `docker-compose exec scrapy-app <command>`

## 📊 Architecture

```
Scrapy → Kafka → kafka-delta-ingest (Rust) → Delta Lake (S3)
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

# Cloud Storage
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

### Kubernetes staged deployments

When deploying to Kubernetes, the Helm chart defaults to a full pipeline release (`scraping-pipeline` in namespace `scraping`). To run each pipeline stage in its own cluster slice:

```bash
# Stage 1 only (Scrapy discovery)
python start.py --env k8s --stage stage1 \
  --set scrapyApp.image.repository=ghcr.io/you/scrapy \
  --set scrapyApp.image.tag=prod

# Stage 2 workers
python start.py --env k8s --stage stage2 \
  --set stage2Worker.image.repository=ghcr.io/you/stage2 \
  --set stage2Worker.image.tag=prod

# Stage 3 workers
python start.py --env k8s --stage stage3 \
  --set stage3Worker.image.repository=ghcr.io/you/stage3 \
  --set stage3Worker.image.tag=prod
```

Use `--stage all-stages` together with `--release-prefix`/`--namespace-prefix` to spin up one release per stage automatically. Make sure you supply valid container image references for each stage.

To remove a staged deployment:

```bash
python shutdown.py --env k8s --stage stage1
```



## 🐛 Troubleshooting

```bash
# Verify Kafka→Prometheus metrics flow
./scripts/verify-kafka-metrics.sh

# Check all services
docker-compose ps

# View logs
docker-compose logs -f kafka-delta-ingestor
```



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
