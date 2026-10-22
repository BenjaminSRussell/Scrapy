<div align="center">

# Web Scraping Pipeline

### *Intelligent, scalable web crawling with real-time monitoring*

[![CI](https://github.com/BenjaminSRussell/Scrapy/actions/workflows/main.yml/badge.svg)](https://github.com/BenjaminSRussell/Scrapy/actions/workflows/main.yml)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/downloads/)
[![Scrapy](https://img.shields.io/badge/scrapy-2.11+-green.svg)](https://scrapy.org/)
[![License](https://img.shields.io/badge/license-MIT-purple.svg)](LICENSE)

[Quick Start](#quick-start) • [Canonical docs](Scraping_project/README.md) • [Guides](Scraping_project/docs/guides/README.md)

</div>

---

This file is the entry point. The canonical project doc is [Scraping_project/README.md](Scraping_project/README.md) (architecture, storage, deployment). Task guides: [Configuration](Scraping_project/docs/guides/CONFIGURATION.md) • [Running](Scraping_project/docs/guides/RUNNING.md) • [Monitoring](Scraping_project/docs/guides/MONITORING.md) • [Data usage](Scraping_project/docs/guides/DATA_USAGE.md). Compose profiles: [Scraping_project/COMPOSE.md](Scraping_project/COMPOSE.md).

**Supported Python is 3.11 and 3.12.** Those are the versions in CI (`.github/workflows/main.yml`). The Docker image is `python:3.11-slim`, and `.python-version` pins 3.11. A newer host interpreter, including 3.14, is not a supported version.

> **Always work from `Scraping_project/`.** `start.py`, `cli.py`, `requirements*.txt`, `pytest.ini`, `docker-compose.yml` and `scrapy.cfg` all live there, so every command in this README assumes you ran `cd Scraping_project` first (#334). From the clone root, `python start.py` fails; `python Scraping_project/start.py` or `cd Scraping_project && python start.py` works.

---

<a id="-quick-start"></a>

## Quick Start

From a fresh clone the repository directory is `Scrapy`. The first command below is from that directory.

```bash
cd Scraping_project
python start.py
```

`python start.py` needs Docker Compose and a host Python 3.11 or 3.12 only to launch it. It enables the Compose `core` and `monitoring` profiles (Redis, scraper running one pipeline pass, stage 2–3 workers, Postgres, Prometheus, Grafana) and waits for Postgres. It does not start Stage 4 (`ml` / `full`), Kafka (`streaming`), or the OpenTelemetry overlay. Same profiles as `docker compose --profile core --profile monitoring up -d`. A bare `docker compose up -d` starts nothing, because every service is behind a profile.

Grafana: **http://localhost:3000** (user `admin`; the password is `GRAFANA_ADMIN_PASSWORD` from [`.env`](Scraping_project/.env.example), default `admin` for local dev only). Prometheus is on `localhost:9090`.

`--reset-delta` wipes Delta tables and reloads seeds. Stop with `python shutdown.py`. Other domains, seeds, and resuming after a crash: [Running guide](Scraping_project/docs/guides/RUNNING.md). Kubernetes: [k8s/README.md](Scraping_project/k8s/README.md) and [DEPLOYMENT.md](Scraping_project/DEPLOYMENT.md).

Virtualenv, pins, and the CI test command: [CONTRIBUTING.md](CONTRIBUTING.md). Use `python3.11 -m venv .venv` inside `Scraping_project/`.

---

## Spiders

Run spiders by their **scrapy name** (not the file name) from `Scraping_project/` (#489):

| `scrapy crawl …` | Module | Status |
|---|---|---|
| `scout` | `src/stage1/scout_spider.py` | default discovery spider |
| `depth` | `src/stage1/experimental/depth_spider.py` | experimental |
| `javascript` | `src/stage1/experimental/js_spider.py` | experimental (Playwright) |
| `deep_dive` | `src/stage1/experimental/deep_dive_spider.py` | experimental |
| `base` | `src/stage1/experimental/base_spider.py` | experimental base class |

```bash
cd Scraping_project
scrapy list            # base, deep_dive, depth, javascript, scout
scrapy crawl scout     # not `scrapy crawl scout_spider`
```

### Usage

```python
from src.core.config import get_config
from src.lakehouse.lakehouse_manager import LakehouseManager
from src.stage1.processors.url_processor import URLProcessor

config = get_config()                                  # config.yml singleton
lake = LakehouseManager.get_instance()                 # Delta Lake tables
processor = URLProcessor("https://www.uconn.edu/", config.get("stage1.allowed_domains", ["uconn.edu"]))
```

Crawl another domain without editing config: `scrapy crawl scout -a allowed_domains=example.org -a start_urls=https://www.example.org/`
([Running guide](Scraping_project/docs/guides/RUNNING.md)).

Config is read with `get_config()` (`src/core/config.py`): environment variable, then `config.yml`, then the default in code. Full key list: [Configuration guide](Scraping_project/docs/guides/CONFIGURATION.md).

```python
from src.core.config import get_config

config = get_config()
redis_host = config.get("redis.host", "localhost")      # dot-notation keys
batch_size = config.get("stage1.batch_size", 50)
```

---

## Storage

Pipeline tables live in Delta Lake under `DELTA_LAKE_PATH` (default `./data/delta_lake`), via `LakehouseManager` (`src/lakehouse/lakehouse_manager.py`). Redis holds seen-URL sets and queues. Postgres is an optional metrics sink. Table list and export options: [Data usage guide](Scraping_project/docs/guides/DATA_USAGE.md). Column contracts for the core tables other repositories read (`pages`, `extractions`, `jobs`): [Delta schema registry](docs/contracts/README.md).

```python
from src.lakehouse.lakehouse_manager import LakehouseManager

lake = LakehouseManager.get_instance()

# Write (async by default; async_write=False commits before returning)
lake.write("stage1_discovery", [{"url": "https://www.uconn.edu/", "url_hash": "abc"}], async_write=False)

# Read with partition pruning, or count
rows = lake.read("stage1_discovery", filters=[("domain", "=", "uconn.edu")], columns=["url"])
total = lake.count("stage1_discovery")

# Stream a table to CSV / JSON lines / Parquet
lake.export("stage1_discovery", "exports/discovery.parquet", format="parquet")

# Drain the write queue and stop the background writer
lake.shutdown()
```

---

## URL processing

`URLProcessor` (`src/stage1/processors/url_processor.py`) discovers, canonicalizes, filters and scores links. Scoring lives in `URLValueAssessor` (`src/common/url_value_assessor.py`).

```python
from scrapy.http import HtmlResponse

from src.stage1.processors.url_processor import URLProcessor

processor = URLProcessor(base_url="https://www.uconn.edu/", allowed_domains=["uconn.edu"])

# Discover + assess the links on a page in one call
response = HtmlResponse(url="https://www.uconn.edu/", body=b'<a href="/research/">Research</a>', encoding="utf-8")
urls = processor.discover_and_assess(response, min_value_score=40)
# [{'url': 'https://www.uconn.edu/research', 'value_score': 70, 'recommended_spider': 'scout', 'reasons': [...], ...}]

# Canonicalize: lowercases, drops tracking params and fragments, sorts the query
processor.normalize_url("https://WWW.UConn.edu/About/?utm_source=x&b=2&a=1#top")
# 'https://www.uconn.edu/about?a=1&b=2'

# Filter static assets and unwanted URLs
processor.should_follow_url("https://www.uconn.edu/logo.png")    # False

# Deduplicate by canonical form
processor.deduplicate_urls(["https://www.uconn.edu/a", "https://www.uconn.edu/a?utm_source=x"])
# ['https://www.uconn.edu/a']

# Crawl priority (0-100)
processor.calculate_priority("https://www.uconn.edu/research/labs", value_score=85, depth=2)
# 75
```

---

## Common commands

```bash
# from Scraping_project/
# Reseed from the bundled CSV (or --csv path/to/urls.csv).
# `python reseed.py` is deprecated; this is the redirect.
python scripts/reseed.py

# Add one seed URL
python cli.py seeds add https://www.example.org/ --note "why" --actor me

# Named seed sets: create, import a CSV under data/raw/, list (URL count, last used), delete
python cli.py seeds set create admissions https://admissions.example.org/ https://apply.example.org/
python cli.py seeds set import admissions data/raw/admissions.csv
python cli.py seeds set list
python cli.py scrapy --spiders scout --seed-set admissions

# Reset Delta tables and reload seeds
python start.py --reset-delta
```

---

## Observability

Control Center (`dashboard/serve.py`, http://localhost:8080) is the crawl console. It does not embed Grafana. Grafana is a separate origin, so panels are deep links, not iframes. The System tab on Control Center reads Redis depth from `/api/queues`. Lag, crawl rate, and Delta commits are the pack dashboard.

From `Scraping_project/`, on a clean host:

```bash
docker compose -f docker-compose.observability.yml up -d
```

| | |
|---|---|
| Pack Grafana | http://127.0.0.1:3001 (user `admin`, password `GRAFANA_ADMIN_PASSWORD`, default `admin` for local dev only) |
| Lag, depth, crawl rate, Delta commits | http://127.0.0.1:3001/d/ops-lag-depth-commits |
| One panel | append `?viewPanel=2` (lag), `3` (Redis depth), `4` (crawl rate), or `5` (Delta commits) |
| Pack Prometheus | http://127.0.0.1:9095 |
| Same dashboard on the monitoring-profile Grafana | http://localhost:3000/d/ops-lag-depth-commits |

The pack scrapes StatsD (`statsd-exporter:9125`, host `127.0.0.1:9125`), the Redis exporter, Scrapy `:9410` and stage workers `:9430` (crawl rate and `lakehouse_manager` compaction), Kafka JMX `:5556`, and `rust-sitemap:9464` when that process is on `scraper-network`. Absent targets stay down and do not page. `KafkaConsumerLagSLO` stays above 250000 messages. `QueueBacklog` stays above 10000. `FrontierGrowth` is the extra climb alert. Loki, Jaeger, and OpenTelemetry still use `--profile observability` on the same file. Detail: [MONITORING.md](MONITORING.md) and [Scraping_project/COMPOSE.md](Scraping_project/COMPOSE.md).

## Security

- Report vulnerabilities **privately**; see [SECURITY.md](SECURITY.md). Don't open a public issue.
- Keep secrets in `Scraping_project/.env` (git-ignored). The [`.env.example`](Scraping_project/.env.example) template lists every variable.
- Compose defaults are for local development only. If one leaked or was used on a shared host, follow [rotating a leaked or default password](SECURITY.md#rotating-a-leaked-or-default-password).

| Policy | |
|---|---|
| 🔒 [SECURITY.md](SECURITY.md) | report vulnerabilities privately; supported versions |
| 🤝 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Contributor Covenant 2.1 |
| 📜 [CHANGELOG.md](CHANGELOG.md) | what changed, by release ([releasing](docs/RELEASING.md)) |
| 🧭 [docs/adr/](docs/adr/README.md) | architecture decision records |

MIT License - see [LICENSE](LICENSE).

Architecture of the pipeline: [Scraping_project/README.md](Scraping_project/README.md#architecture). There is no separate `ARCHITECTURE.md`.
