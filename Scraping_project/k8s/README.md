# Kubernetes Deployment - Scraping Pipeline

This directory contains Kubernetes manifests for deploying the UConn Scraping Pipeline.

**Supported install path: the Helm chart** (`helm/scraping-pipeline`, below).
`deployment.yaml` is a minimal quick-start: stage1/stage2 workers and Redis, with no Postgres, Kafka, monitoring or network policies.
It keeps probe parity with the chart (#181):
- **Liveness:** `python -m src.utils.probe alive <worker>`.
- **Readiness:** `python -m src.utils.probe ready-redis`.
- **Shutdown:** a preStop grace sleep.

`src/utils/probe.py` is stdlib-only, because the `python:3.11-slim` image has no `pgrep`.

## Quick Start

```bash
# 1. Create namespace
kubectl create namespace scraping-pipeline

# 2. Create secrets
kubectl create secret generic postgres-credentials \
  --from-literal=password='YOUR_PASSWORD' \
  -n scraping-pipeline


kubectl create secret generic grafana-credentials \
  --from-literal=admin-user='admin' \
  --from-literal=admin-password='YOUR_PASSWORD' \
  -n scraping-pipeline

# 3. Customize values
cp helm/scraping-pipeline/values.yaml values-custom.yaml
# Edit values-custom.yaml with your settings

# 4. Install
helm install scraping-pipeline helm/scraping-pipeline \
  -f values-custom.yaml \
  -n scraping-pipeline
```

## Directory Structure

```
k8s/
├── helm/
│   └── scraping-pipeline/         # Helm chart
│       ├── Chart.yaml             # Chart metadata
│       ├── values.yaml            # Default values
│       └── templates/             # Kubernetes manifests
│           ├── _helpers.tpl       # Template helpers
│           ├── secrets.yaml       # Secret management
│           ├── redis-statefulset.yaml
│           ├── kafka-statefulset.yaml
│           ├── scrapy-deployment.yaml
│           └── delta-lake-pvc.yaml
└── README.md                      # This file
```

Detailed deployment steps: [../DEPLOYMENT.md](../DEPLOYMENT.md#kubernetes-deployment).

## Architecture

### Stateful Services (StatefulSets)
- **Redis**: Message queue and caching (1 replica)
- **PostgreSQL**: Metrics database (1 replica)
- **Zookeeper**: Kafka coordination (1 replica)
- **Kafka**: Event streaming (1+ replicas)

- **Prometheus**: Metrics collection (2 replicas for HA)
- **Alertmanager**: Alert management (3 replicas for HA)
- **Grafana**: Dashboards (1 replica)

### Stateless Services (Deployments)
- **Scrapy App**: Web crawling (scalable)
- **Stage 2 Worker**: Page analysis (scalable)
- **Stage 3 Worker**: Summarization (scalable)
- **Stage 4 Worker**: Large documents / PDF + OCR (`stage4Worker`, **off by default**; see below)
- **Kafka Delta Ingestor**: Streaming to Delta Lake (scalable)
- **Metrics Exporter**: Custom metrics (1 replica)
- **Exporters**: Redis, PostgreSQL, Kafka JMX, StatsD

## Configuration

### Image Registry

Update in `values-custom.yaml`:
```yaml
scrapyApp:
  image:
    repository: your-registry.com/scraping-pipeline/scrapy-app
    tag: v1.0.0
```

### Storage

Ensure your cluster has:
1. **ReadWriteOnce** storage for StatefulSets (standard SSD)
2. **ReadWriteMany** storage for Delta Lake (NFS, EFS, etc.)

```yaml
global:
  storageClass: "fast-ssd"

deltaLake:
  persistence:
    storageClass: "efs-sc"  # Must support ReadWriteMany
```

### Secrets

**Production**: Use External Secrets Operator or your cloud provider's secret manager.

```yaml
secrets:
  useExternalSecrets: true
  externalSecretsProvider: "aws-secrets-manager"
```

### Scaling

```yaml
# Horizontal scaling
scrapyApp:
  replicaCount: 3

stage2Worker:
  replicaCount: 5

# Resource scaling
scrapyApp:
  resources:
    limits:
      cpu: 16000m
      memory: 32Gi
```

### Deploying stages separately (`start.py --stage`)

`python start.py --env k8s --stage <stage>` deploys one stage as its own release and
namespace. Every stage sets all four workload toggles explicitly, so a stage release
never depends on chart defaults (#504):

| `--stage` | scrapyApp | stage2Worker | stage3Worker | stage4Worker |
|---|---|---|---|---|
| `stage1` | on | off | off | off |
| `stage2` | off | on | off | off |
| `stage3` | off | off | on | off |
| `stage4` | off | off | off | on |
| `all-stages` | one release per row above (stage1 → stage4) | | | |
| `pipeline` (default) | chart defaults: Stage 4 off | | | |

Stage 4 needs an image with the PDF/OCR toolchain (`stage4Worker.image`), so it's off
in `values.yaml`. Include it in a full-pipeline release with
`--set stage4Worker.enabled=true`. Preview any of these without touching the cluster:
`python start.py --env k8s --stage stage4 --dry-run`.

## Monitoring

### Prometheus Metrics

Access Prometheus:
```bash
kubectl port-forward svc/scraping-pipeline-prometheus-a 9090:9090 -n scraping-pipeline
```

#### What gets scraped (#789)

The bundled Prometheus uses its own scrape config (no prometheus-operator, so
no ServiceMonitor). Pod annotations are set too, for an external
annotation-based Prometheus.

| Job | Target | Port | Discovery |
|-----|--------|------|-----------|
| `scrapy_app` | Scrapy pods | 9410-9419 | Service + `prometheus.io/*` annotations |
| `stage2_worker`, `stage3_worker` | every worker pod | `workerMetrics.port` (9430) | headless `<release>-stageN-metrics` Service, `dns_sd_configs` (one target per replica) + `prometheus.io/*` annotations |
| `scraping_pipeline` | metrics-exporter | 9100 | Service |
| `redis`, `postgres`, `kafka_jmx` | exporters | 9121 / 9187 / 5556 | Service |
| `statsd` | statsd-exporter (kafka-delta-ingestor pushes StatsD) | 9102 | Service |

Workers start the endpoint from `src/utils/worker_metrics.py`
(`WORKER_METRICS_ENABLED`, `WORKER_METRICS_PORT`, `WORKER_METRICS_ADDR`); a
port clash is logged and the worker keeps consuming. Set
`workerMetrics.enabled=false` to drop the port, annotations, Services and jobs.
With `networkPolicy.enabled=true`, each target gets a
`<target>-metrics-ingress` policy admitting only Prometheus on its metrics
port(s); default-deny previously blocked every scrape.

### Grafana Dashboards

Grafana's Service defaults to **ClusterIP** (#233). Do **not** flip it to
`LoadBalancer` without SSO or an IP allowlist: that puts the admin UI on a public
address. Reach it with:

```bash
kubectl port-forward svc/scraping-pipeline-grafana 3000:3000 -n scraping-pipeline
# or via the TLS ingress below (values-prod.yaml)
```

`profile: production` (or `-f values-prod.yaml`) **fails to render** when Grafana is a
LoadBalancer without `grafana.service.allowPublicLoadBalancer=true`, or when the
ingress is enabled without `ingress.tls` (#451). See the Security section.

### Logs

```bash
# View scrapy logs
kubectl logs -f deployment/scraping-pipeline-scrapy -n scraping-pipeline

# View all logs for a component
kubectl logs -f -l app.kubernetes.io/component=scrapy -n scraping-pipeline
```

### Graceful shutdown and Kafka delivery

On SIGTERM, Scrapy closes the spider and `KafkaPipeline` flushes the producer
for `KAFKA_CLOSE_FLUSH_TIMEOUT` seconds (default 30). Anything still
undelivered after that, plus produce/delivery failures during the crawl, is
written to `KAFKA_SPILL_DIR/<topic>.jsonl` (default `data/kafka_spill/`) and
counted in `kafka_produce_failures_total{reason}` /
`kafka_spilled_messages_total`, instead of being dropped.

Keep `scrapyApp.terminationGracePeriodSeconds` (Helm default 120) comfortably
above that flush timeout plus the rest of spider shutdown. If the kubelet
SIGKILLs the pod mid-flush, neither the flush nor the spill can finish. Put
the spill directory on a persistent volume if spilled messages must survive
pod replacement.

## Upgrading

```bash
# Update image tags in values-custom.yaml
# Then upgrade
helm upgrade scraping-pipeline helm/scraping-pipeline \
  -f values-custom.yaml \
  -n scraping-pipeline
```

## Uninstall

```bash
# Delete Helm release
helm uninstall scraping-pipeline -n scraping-pipeline

# Delete namespace and all resources
kubectl delete namespace scraping-pipeline
```

## Documentation

- **[DEPLOYMENT.md](../DEPLOYMENT.md#kubernetes-deployment)**: Comprehensive deployment guide
- **[values.yaml](helm/scraping-pipeline/values.yaml)**: Configuration options
- **[Chart.yaml](helm/scraping-pipeline/Chart.yaml)**: Chart metadata

## Security

- **Grafana:** ClusterIP by default; prefer the TLS ingress in `values-prod.yaml`
  (cert-manager annotation + `ingress.tls`). Never leave a LoadBalancer on without
  SSO / an IP allowlist (#233 / #451).
- **Kafka durability:** the chart ships RF=1 for a single local broker. Production
  must use `values-prod.yaml` (3 brokers, RF=3, minISR=2). Producers already use
  `acks=all` (#174); with RF=1 that only waits for the leader (#176).
- **Secrets:** never put credentials in values committed to git; use
  `existingSecret` (Grafana) and Kubernetes Secrets. See the root `SECURITY.md`.

## Support

For issues or questions:
1. Check pod logs: `kubectl logs <pod-name> -n scraping-pipeline`
2. Check events: `kubectl get events -n scraping-pipeline`
3. Verify resources: `kubectl get all -n scraping-pipeline`
4. Review configuration: `helm get values scraping-pipeline -n scraping-pipeline`

## Development vs Production

### Development
```yaml
# Minimal resources, no persistence
redis:
  persistence:
    enabled: false
  resources:
    limits:
      cpu: 500m
      memory: 512Mi
```

### Production

Use the production overlay (TLS for Grafana, Kafka RF=3 / minISR=2, ClusterIP Grafana):

```bash
helm upgrade --install scraping-pipeline ./k8s/helm/scraping-pipeline \
  -f ./k8s/helm/scraping-pipeline/values.yaml \
  -f ./k8s/helm/scraping-pipeline/values-prod.yaml \
  --set ingress.hosts[0].host=grafana.example.edu \
  --set ingress.tls[0].hosts[0]=grafana.example.edu \
  -n scraping-pipeline
```

`values-prod.yaml` also sets `profile: production`, which turns the exposure and
durability checks into hard render failures (#176 / #233 / #451):

| Guardrail | Development default | Production requirement |
|---|---|---|
| Grafana Service | ClusterIP | ClusterIP (or LoadBalancer only with `allowPublicLoadBalancer`) |
| Ingress TLS | empty (plain HTTP ok locally) | non-empty `ingress.tls` + cert-manager annotation example |
| Kafka RF / ISR | 1 / 1 | RF ≥ 2 (3 recommended), `minInsyncReplicas` = RF − 1, `kafka.replicas` ≥ RF |

```yaml
# HA, persistence, proper resources
redis:
  persistence:
    enabled: true
    size: 20Gi
    storageClass: "fast-ssd"
  resources:
    limits:
      cpu: 2000m
      memory: 4Gi
```

## Requirements

- Kubernetes 1.24+
- Helm 3.10+
- kubectl configured
- Container registry access
- Minimum: 16 CPUs, 32GB RAM, 500GB storage
- Recommended: 32 CPUs, 64GB RAM, 1TB+ storage
