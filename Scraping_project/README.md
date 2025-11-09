# UConn Web Scraping Pipeline

A production-grade, type-safe, resilient web scraping pipeline designed for large-scale institutional data collection and analysis.

## Overview

This is an enterprise-ready multi-stage web scraping system with comprehensive type safety, error handling, caching, and production deployment configurations. The pipeline has evolved through 10 major phases to deliver a scalable, maintainable, and observable system.

## Key Features

### Production-Ready Infrastructure (Phase 10)
- Docker and Kubernetes deployment configurations
- CI/CD pipeline with GitHub Actions
- Prometheus + Grafana monitoring stack
- Automated testing and deployment
- Health checks and alerting

### Type Safety & Data Validation (Phase 6)
- Pydantic models for runtime validation
- MyPy static type checking
- PyArrow schemas for Delta Lake tables
- Type-safe operations throughout the pipeline

### Error Handling & Resilience (Phase 7)
- Hierarchical exception system with categorization
- Retry logic with exponential backoff and jitter
- Circuit breaker pattern for fault tolerance
- Dead letter queue for failed item management
- Comprehensive error context tracking

### Performance & Caching (Phase 8)
- Multi-level caching (L1 memory + L2 Redis)
- HTTP connection pooling
- Performance profiling utilities
- Cache statistics and hit rate tracking

### Comprehensive Testing (Phase 9)
- 25+ unit tests with 100% pass rate
- Pytest configuration with async support
- Mock fixtures for Redis and Delta Lake
- Test coverage tracking

### Multi-Stage Architecture
- **Stage 1**: Discovery and URL extraction using Scrapy spiders
- **Stage 2**: Content analysis and validation
- **Stage 3**: Entity summarization and aggregation
- **Stage 4**: Advanced processing and enrichment

### Data Storage
- **Delta Lake**: Primary data lake for scalable storage with schema validation
- **Redis**: Distributed deduplication, caching, and queue management
- **Type-safe operations**: All data validated with Pydantic models

## Quick Start

### Prerequisites
- Docker 20.10+ and Docker Compose 2.0+
- Python 3.11+
- Kubernetes 1.24+ (for K8s deployment)

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd Scraping_project
```

2. Install dependencies:
```bash
pip install -r requirements.txt
pip install -r dev-requirements.txt  # For development
```

3. Start infrastructure with Docker Compose:
```bash
docker-compose up -d
```

This starts:
- Redis (caching and queue management)
- Stage 1-4 workers
- Prometheus (metrics)
- Grafana (dashboards)

### Running the Pipeline

#### Option 1: Docker Compose (Recommended)
```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f stage2-worker

# Scale workers
docker-compose up -d --scale stage2-worker=3
```

#### Option 2: Kubernetes Deployment
```bash
# Deploy to Kubernetes
kubectl apply -f k8s/deployment.yaml

# Check status
kubectl get pods -n uconn-scraper

# Scale workers
kubectl scale deployment stage2-worker --replicas=5 -n uconn-scraper
```

#### Option 3: Individual Components
```bash
# Run Stage 1 worker
python -m src.workers.stage1_worker

# Run Stage 2 worker
python -m src.workers.stage2_worker

# Run with custom config
REDIS_HOST=localhost DELTA_LAKE_PATH=/data python -m src.workers.stage2_worker
```

## Architecture

### Pipeline Flow

```
Seed URLs → Stage 1 (Discovery) → Stage 2 (Analysis) →
Stage 3 (Summarization) → Stage 4 (Advanced) → Final Output
```

### Component Overview

#### Stage 1: Discovery
- **ScoutSpider**: Rapid breadth-first discovery
- **DepthSpider**: Depth-first focused crawling
- **JSSpider**: JavaScript-rendered content extraction
- **Output**: `seed_urls`, `js_spider_queue`, `stage2_queue`

#### Stage 2: Content Analysis
- Validates and analyzes discovered content
- Filters and classifies pages
- Extracts structured data with validation
- **Output**: Validated content for Stage 3

#### Stage 3: Summarization
- Entity grouping and recency-weighted aggregation
- LLM-based summarization
- Temporal relevance scoring
- **Output**: Entity summaries

#### Stage 4: Advanced Processing
- ML-based classification
- Relationship extraction
- Final enrichment
- **Output**: Enriched final data

### Type Safety (Phase 6)

All data is validated using Pydantic models:

```python
from src.core.models import URLRecord, Stage2Analysis

# Type-safe URL record
url_record = URLRecord(
    url="https://example.com",
    url_hash="abc123..."
)

# Validated analysis data
analysis = Stage2Analysis(
    url="https://example.com",
    url_hash="abc123...",
    word_count=500,
    quality_score=0.85,
    processed_at=datetime.now()
)
```

### Error Handling (Phase 7)

Robust error handling with retry and circuit breaker:

```python
from src.utils.retry import with_retry, CircuitBreaker
from src.core.exceptions import NetworkError

# Automatic retry with exponential backoff
@with_retry(max_attempts=3, base_delay=1.0)
async def fetch_url(url: str):
    # Your code here
    pass

# Circuit breaker for fault tolerance
cb = CircuitBreaker(failure_threshold=5, name="api")

@with_retry(circuit_breaker=cb)
async def call_api():
    # API call
    pass
```

### Performance Optimization (Phase 8)

Built-in caching and profiling:

```python
from src.utils.cache import cached
from src.utils.profiler import profile

# Cache expensive function results
@cached(ttl=3600)
async def expensive_operation(key: str):
    # Expensive computation
    return result

# Profile execution time
@profile
async def process_data(data):
    # Processing logic
    pass
```

## Configuration

### Environment Variables

```bash
# Redis
REDIS_HOST=redis-service
REDIS_PORT=6379

# Delta Lake
DELTA_LAKE_PATH=/data/delta

# Logging
LOG_LEVEL=INFO

# Workers
WORKERS=4
CONCURRENCY=10
```

### Docker Configuration

Edit `docker-compose.yml` to customize:
- Worker replicas
- Memory limits
- CPU allocation
- Port mappings

### Kubernetes Configuration

Edit `k8s/deployment.yaml` for:
- Auto-scaling policies
- Resource requests/limits
- Persistent volume sizes
- Service configuration

## Testing

### Run All Tests

```bash
# Run test suite
pytest

# With coverage
pytest --cov=src --cov-report=html

# Verbose output
pytest -v
```

### Run Specific Test Suites

```bash
# Cache tests
pytest tests/test_cache.py -v

# Model validation tests
pytest tests/test_models.py -v

# Retry and circuit breaker tests
pytest tests/test_retry.py -v
```

### Test Results

Current test status:
- **25 tests passing** ✓
- Cache layer: 6/6 tests passing
- Model validation: 10/10 tests passing
- Retry/circuit breaker: 9/9 tests passing

## Monitoring

### Prometheus Metrics

Available at `http://localhost:9090`:

- `pipeline_errors_total`: Total pipeline errors
- `cache_hits_total`: Cache hit count
- `cache_misses_total`: Cache miss count
- `retry_attempts_total`: Retry attempts
- `circuit_breaker_state`: Circuit breaker state (0=closed, 1=open, 2=half-open)

### Grafana Dashboards

Access at `http://localhost:3000` (admin/admin):

- **Pipeline Overview**: End-to-end metrics
- **Worker Performance**: Per-worker statistics
- **Cache Performance**: Hit rates and latency
- **Error Rates**: Error tracking and alerting

### Health Checks

```bash
# Check worker health
curl http://localhost:8000/health

# Check Redis
redis-cli ping

# Check Prometheus
curl http://localhost:9090/-/healthy
```

## Deployment

### Local Development

```bash
docker-compose up -d
```

### Production Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for comprehensive deployment guide including:
- Docker image building
- Kubernetes deployment
- Scaling strategies
- Backup procedures
- Security best practices

### CI/CD Pipeline

GitHub Actions workflow automatically:
1. Runs tests on PR and push
2. Performs type checking with mypy
3. Builds Docker images
4. Deploys to Kubernetes (on main branch)

Required secrets:
- `DOCKER_USERNAME`
- `DOCKER_PASSWORD`
- `KUBE_CONFIG`

## Data Storage

### Delta Lake Tables

All tables use PyArrow schemas for validation:

| Table | Description | Schema |
|-------|-------------|--------|
| `seed_urls` | Initial seed URLs | URLRecord |
| `discovered_urls` | All discovered URLs | URLRecord |
| `stage2_queue` | Pages for analysis | Stage2Analysis |
| `stage3_queue` | Summarization queue | Stage3Summary |
| `errors` | Error tracking | ErrorRecord |

### Type-Safe Operations

```python
from src.utils.delta import DeltaLakeHelper
from src.core.models import Stage2Analysis

delta = DeltaLakeHelper()

# Write with validation
data = [Stage2Analysis(...)]
delta.write_typed("stage2_queue", data, Stage2Analysis)

# Read with validation
validated_data = delta.read_typed("stage2_queue", Stage2Analysis)
```

## Project Structure

```
Scraping_project/
├── src/
│   ├── core/                    # Core infrastructure
│   │   ├── models.py            # Pydantic models (Phase 6)
│   │   ├── schemas.py           # PyArrow schemas (Phase 6)
│   │   └── exceptions.py        # Exception hierarchy (Phase 7)
│   ├── utils/                   # Utilities
│   │   ├── cache.py             # Caching (Phase 8)
│   │   ├── retry.py             # Retry/circuit breaker (Phase 7)
│   │   ├── dead_letter_queue.py # DLQ (Phase 7)
│   │   ├── connection_pool.py   # Connection pooling (Phase 8)
│   │   ├── profiler.py          # Profiling (Phase 8)
│   │   └── delta.py             # Delta Lake helpers
│   ├── workers/                 # Worker processes
│   │   ├── stage1_worker.py
│   │   ├── stage2_worker.py
│   │   ├── stage3_worker.py
│   │   └── stage4_worker.py
│   └── stage1/                  # Spiders
│       ├── scout_spider.py
│       ├── depth_spider.py
│       └── js_spider.py
├── tests/                       # Test suite (Phase 9)
│   ├── conftest.py              # Pytest configuration
│   ├── test_cache.py
│   ├── test_models.py
│   ├── test_retry.py
│   └── README.md
├── k8s/                         # Kubernetes (Phase 10)
│   └── deployment.yaml
├── monitoring/                  # Monitoring (Phase 10)
│   ├── prometheus.yml
│   └── alerts.yml
├── .github/workflows/           # CI/CD (Phase 10)
│   └── ci-cd.yml
├── docker-compose.yml           # Docker Compose (Phase 10)
├── Dockerfile                   # Docker build (Phase 10)
├── mypy.ini                     # Type checking config (Phase 6)
├── pytest.ini                   # Test configuration (Phase 9)
├── requirements.txt             # Python dependencies
└── DEPLOYMENT.md               # Deployment guide (Phase 10)
```

## Development Evolution

This pipeline evolved through 10 major phases:

1. **Phase 1-3**: Initial code organization and file structure
2. **Phase 4-5**: Import updates and cleanup
3. **Phase 6**: Type safety with Pydantic and MyPy
4. **Phase 7**: Error handling and resilience patterns
5. **Phase 8**: Performance optimization and caching
6. **Phase 9**: Comprehensive testing infrastructure
7. **Phase 10**: Production deployment configurations

See `EVOLUTION_ROADMAP.md` for detailed phase documentation.

## Troubleshooting

### Common Issues

**Issue**: Tests failing with validation errors
```bash
# Solution: Check model definitions
pytest tests/test_models.py -v
```

**Issue**: Redis connection errors
```bash
# Solution: Check Redis is running
docker-compose ps redis
docker-compose restart redis
```

**Issue**: Circuit breaker open
```bash
# Solution: Check error logs and reset if needed
# Circuit breaker will auto-recover after timeout
```

**Issue**: Cache misses
```bash
# Solution: Check Redis memory and TTL settings
redis-cli INFO memory
```

### Debug Mode

Enable debug logging:
```bash
export LOG_LEVEL=DEBUG
python -m src.workers.stage2_worker
```

## Performance Tuning

### Worker Scaling

Recommended configuration:
- **Stage 1**: 1 instance (I/O bound)
- **Stage 2**: 2-5 instances (CPU bound)
- **Stage 3**: 1-2 instances (API limited)
- **Stage 4**: 1 instance (memory intensive)

### Cache Tuning

Adjust cache settings in `src/utils/cache.py`:
```python
cache = SmartCache(
    redis_client=redis,
    strategy=CacheStrategy.LRU,
    max_local_size=1000,    # L1 cache size
    default_ttl=3600        # 1 hour TTL
)
```

### Connection Pool

Configure in `src/utils/connection_pool.py`:
```python
pool = ConnectionPool(
    factory=create_connection,
    min_size=5,
    max_size=20,
    timeout=30.0
)
```

## Contributing

1. Create a feature branch
2. Write tests for new functionality
3. Ensure all tests pass: `pytest`
4. Run type checking: `mypy src/`
5. Submit a pull request

## License

[Your License Here]

## Contact

[Your Contact Information]

---

**Production Status**: ✓ Ready for deployment

Last Updated: 2025-11-09
