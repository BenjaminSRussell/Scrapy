# Scraping Pipeline

Resilient web crawling with a side of real-time telemetry. Think of it as a spider factory: Scrapy sends discoveries through Kafka, a Rust worker pours batches into Delta Lake, and Prometheus/Grafana keep score so you know what the swarm is doing.

[![CI](https://github.com/benjaminrussell/Scraping_project/actions/workflows/main.yml/badge.svg)](https://github.com/benjaminrussell/Scraping_project/actions/workflows/main.yml)

---

## TL;DR
- `python start.py` boots the full Docker Compose stack (Scrapy + Kafka + Delta Lake + monitoring).
- Redis keeps the crawl queue honest, Kafka streams every find, and a Rust daemon lands it in Delta tables.
- Prometheus/Grafana come pre-wired; open http://localhost:3000 (admin/admin) to watch it work.
- Need to stop everything? `python shutdown.py` (add `--purge-data` when you want a clean slate).

---

## Run It Locally
```bash
# fire everything up
python start.py

# optional: reset Delta tables and reload seed URLs
python start.py --reset-delta

# tear it down
python shutdown.py

# nuke containers + volumes + Delta data
python shutdown.py --purge-data
```

### Handy one-offs
- Tail logs: `docker-compose logs -f scrapy-app`
- Jump into a container: `docker-compose exec scrapy-app bash`
- Load new seeds: `docker-compose exec scrapy-app python cli.py load_seeds path/to/urls.csv`

---

## Dashboards & Endpoints
| What | Where | Notes |
| --- | --- | --- |
| Grafana | http://localhost:3000 | admin / admin |
| Prometheus A | http://localhost:9091 | scrape target for custom dashboards |
| Redis queue depth | http://localhost:9090/metrics | emitted by `monitoring/metrics_exporter.py` |
| Scrapy metrics | http://localhost:9410/metrics | per-spider Prometheus counters |
| Kafka JMX | http://localhost:5556/metrics | exposed via JMX exporter |

---

## What Lives Where
| Path | Why you care |
| --- | --- |
| `src/stage1/` | Scout spider and shared crawling primitives |
| `monitoring/` | Prometheus config, alerting rules, Grafana provisioning, custom metrics exporter |
| `kafka-delta-ingest/` | Rust crate that writes Kafka batches to Delta Lake |
| `k8s/` | Helm chart + manifests if you’d rather run this on a cluster |
| `scripts/` | Operational helpers (diagnostics, resets, Kubernetes maintenance) |
| `data/` | Local Delta tables and seed CSVs |

---

## Kubernetes (When You’re Ready)
- `python start.py --env k8s --stage pipeline` deploys the whole stack using the bundled Helm chart.
- Want per-stage releases? Use `--stage stage1|stage2|stage3|all-stages` alongside `--release-prefix` and `--namespace-prefix`.
- Full details (including required secrets and storage classes) live in `k8s/README.md` and `k8s/DEPLOYMENT_GUIDE.md`.

---

## Development Loop
```bash
python -m venv .venv
source .venv/bin/activate
pip-sync requirements.txt dev-requirements.txt
pytest
```

- Linting: `ruff check .`
- Reseed Redis + Delta: `python reseed.py`
- Regenerate lock files: `pip-compile requirements.in` and `pip-compile dev-requirements.in`

---

## Troubleshooting Cheatsheet
- `docker-compose ps` — quick status check
- `./scripts/diagnose_issues.sh` — guided health check across services
- `docker-compose logs -f kafka-delta-ingestor` — verify Kafka → Delta flow
- Grafana dashboard not loading? Try `./scripts/reset_grafana_complete.sh`

---

Happy crawling! 🕷️ Drop a Grafana annotation when you ship changes so future-you remembers what happened.
