# Kubernetes Deployment Guide - Scraping Pipeline

## Overview

This guide walks through deploying the UConn Scraping Pipeline to Kubernetes using Helm charts.

## Prerequisites

### Required Tools
- `kubectl` (v1.24+)
- `helm` (v3.10+)
- Access to a Kubernetes cluster (v1.24+)
- Container registry access (Docker Hub, ECR, GCR, etc.)

### Cluster Requirements
- **Minimum**: 16 CPUs, 32GB RAM, 500GB storage
- **Recommended**: 32 CPUs, 64GB RAM, 1TB storage
- Storage classes for both ReadWriteOnce (RWO) and ReadWriteMany (RWX) volumes

## Architecture Overview

### Stateful Services (StatefulSets)
- **Redis**: Message queue and caching
- **PostgreSQL**: Metrics database
- **Zookeeper**: Kafka coordination
- **Kafka**: Event streaming
- **Prometheus** (2 replicas): Metrics collection (HA)
- **Alertmanager** (3 replicas): Alert management (HA cluster)
- **Grafana**: Dashboards and visualization

### Stateless Services (Deployments)
- **Scrapy App**: Web crawler (scalable)
- **Stage 2 Worker**: Page analysis (scalable)
- **Stage 3 Worker**: Summarization (scalable)
- **Kafka Delta Ingestor**: High-performance streaming to Delta Lake
- **Metrics Exporter**: Custom metrics collector
- **Exporters**: Redis, PostgreSQL, Kafka JMX, StatsD

## Step-by-Step Deployment

### 1. Prepare Container Images

Build and push your application images to your container registry:

```bash
# Set your registry
export REGISTRY="your-registry.com/scraping-pipeline"

# Build images
docker build -t $REGISTRY/scrapy-app:v1.0.0 --target crawler .
docker build -t $REGISTRY/kafka-delta-ingest:v1.0.0 --target kafka-delta-ingest .
docker build -t $REGISTRY/metrics-exporter:v1.0.0 --target metrics .

# Push images
docker push $REGISTRY/scrapy-app:v1.0.0
docker push $REGISTRY/kafka-delta-ingest:v1.0.0
docker push $REGISTRY/metrics-exporter:v1.0.0
```

### 2. Configure Storage Classes

Ensure your cluster has appropriate storage classes:

```bash
# List available storage classes
kubectl get storageclass

# Example storage classes needed:
# - fast-ssd (for Kafka, PostgreSQL, Redis)
# - standard (for Prometheus, logs)
# - shared-nfs or efs-csi (for Delta Lake - needs ReadWriteMany)
```

If using AWS EKS, you might need:
```bash
# Install EFS CSI driver for ReadWriteMany volumes
kubectl apply -k "github.com/kubernetes-sigs/aws-efs-csi-driver/deploy/kubernetes/overlays/stable/?ref=release-1.5"
```

### 3. Create Namespace

```bash
kubectl create namespace scraping-pipeline
kubectl config set-context --current --namespace=scraping-pipeline
```

### 4. Configure Secrets

**⚠️ IMPORTANT**: Never commit secrets to version control!

#### Option A: Create secrets manually (Development)

```bash
# PostgreSQL password
kubectl create secret generic postgres-credentials \
  --from-literal=password='YOUR_SECURE_PASSWORD_HERE'


# Grafana credentials
kubectl create secret generic grafana-credentials \
  --from-literal=admin-user='admin' \
  --from-literal=admin-password='YOUR_SECURE_PASSWORD_HERE'
```

#### Option B: Use External Secrets (Production)

Install External Secrets Operator:
```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets -n external-secrets-system --create-namespace
```

Then configure to pull from your secret manager (AWS Secrets Manager, Vault, etc.)

### 5. Customize Values

Create a custom values file:

```bash
cat > values-prod.yaml <<EOF
# Image settings
scrapyApp:
  image:
    repository: your-registry.com/scraping-pipeline/scrapy-app
    tag: v1.0.0

stage2Worker:
  image:
    repository: your-registry.com/scraping-pipeline/scrapy-app
    tag: v1.0.0
  replicaCount: 3

stage3Worker:
  image:
    repository: your-registry.com/scraping-pipeline/scrapy-app
    tag: v1.0.0
  replicaCount: 2

kafkaDeltaIngestor:
  image:
    repository: your-registry.com/scraping-pipeline/kafka-delta-ingest
    tag: v1.0.0
  replicaCount: 2

metricsExporter:
  image:
    repository: your-registry.com/scraping-pipeline/metrics-exporter
    tag: v1.0.0

# Storage classes
global:
  storageClass: "fast-ssd"

deltaLake:
  persistence:
    storageClass: "efs-sc"  # Must support ReadWriteMany
    size: 500Gi

# Ingress
ingress:
  enabled: true
  className: "nginx"
  hosts:
    - host: scraping-pipeline.your-domain.com
      paths:
        - path: /grafana
          pathType: Prefix
          service: grafana
          port: 3000
  tls:
    - secretName: scraping-pipeline-tls
      hosts:
        - scraping-pipeline.your-domain.com

# Resource scaling for production
kafka:
  config:
    defaultReplicationFactor: 3
  persistence:
    size: 200Gi
  resources:
    limits:
      cpu: 4000m
      memory: 8Gi

postgresql:
  persistence:
    size: 50Gi
  resources:
    limits:
      cpu: 4000m
      memory: 8Gi

# Monitoring
prometheus:
  replicas: 2
  persistence:
    size: 100Gi

alertmanager:
  replicas: 3
EOF
```

### 6. Install Helm Chart

```bash
# Add custom values and install
helm install scraping-pipeline ./k8s/helm/scraping-pipeline \
  -f values-prod.yaml \
  --namespace scraping-pipeline \
  --create-namespace

# Or upgrade if already installed
helm upgrade scraping-pipeline ./k8s/helm/scraping-pipeline \
  -f values-prod.yaml \
  --namespace scraping-pipeline
```

### 7. Verify Deployment

```bash
# Check all pods
kubectl get pods -n scraping-pipeline

# Check stateful sets
kubectl get statefulsets -n scraping-pipeline

# Check persistent volumes
kubectl get pvc -n scraping-pipeline

# Check services
kubectl get svc -n scraping-pipeline

# Watch pod status
watch kubectl get pods -n scraping-pipeline
```

Expected output:
```
NAME                                    READY   STATUS    RESTARTS   AGE
scraping-pipeline-redis-0               1/1     Running   0          5m
scraping-pipeline-postgresql-0          1/1     Running   0          5m
scraping-pipeline-zookeeper-0           1/1     Running   0          5m
scraping-pipeline-kafka-0               1/1     Running   0          4m
scraping-pipeline-prometheus-a-0        1/1     Running   0          3m
scraping-pipeline-prometheus-b-0        1/1     Running   0          3m
scraping-pipeline-alertmanager-0        1/1     Running   0          3m
scraping-pipeline-alertmanager-1        1/1     Running   0          3m
scraping-pipeline-alertmanager-2        1/1     Running   0          3m
scraping-pipeline-grafana-0             1/1     Running   0          3m
scraping-pipeline-scrapy-xxxxx          1/1     Running   0          2m
scraping-pipeline-stage2-worker-xxxxx   1/1     Running   0          2m
scraping-pipeline-stage3-worker-xxxxx   1/1     Running   0          2m
```

### 8. Access Services

#### Grafana Dashboard
```bash
# Port forward for local access
kubectl port-forward svc/scraping-pipeline-grafana 3000:3000 -n scraping-pipeline

# Open http://localhost:3000
# Login: admin / (password from secret)
```

#### Prometheus
```bash
kubectl port-forward svc/scraping-pipeline-prometheus-a 9090:9090 -n scraping-pipeline
# Open http://localhost:9090
```



### 9. Initialize Delta Lake

Create initial seed URLs table:

```bash
# Exec into scrapy pod
kubectl exec -it deployment/scraping-pipeline-scrapy -n scraping-pipeline -- bash

# Inside pod
python << EOF
from src.common.delta_lake import DeltaLakeManager

delta = DeltaLakeManager.get_instance()
seed_urls = [
    {'url': 'https://example.com', 'priority': 1}
]
delta.write('seed_urls', seed_urls)
print("Seed URLs initialized!")
EOF

exit
```

## Scaling

### Horizontal Scaling

```bash
# Scale scrapy workers
kubectl scale deployment scraping-pipeline-scrapy --replicas=3 -n scraping-pipeline

# Scale stage2 workers
kubectl scale deployment scraping-pipeline-stage2-worker --replicas=5 -n scraping-pipeline

# Scale stage3 workers
kubectl scale deployment scraping-pipeline-stage3-worker --replicas=4 -n scraping-pipeline
```

### Vertical Scaling

Update resources in `values-prod.yaml`:
```yaml
scrapyApp:
  resources:
    limits:
      cpu: 16000m
      memory: 32Gi
```

Then upgrade:
```bash
helm upgrade scraping-pipeline ./k8s/helm/scraping-pipeline -f values-prod.yaml
```

## Monitoring

### View Metrics

Access Prometheus and query:
```promql
# URLs discovered per minute
rate(scrapy_urls_discovered_total[5m])

# Error rate
rate(scrapy_errors_total[5m])

# Kafka lag
kafka_consumergroup_lag

# Redis memory usage
redis_memory_used_bytes
```

### View Logs

```bash
# Scrapy logs
kubectl logs -f deployment/scraping-pipeline-scrapy -n scraping-pipeline

# Kafka logs
kubectl logs -f statefulset/scraping-pipeline-kafka -n scraping-pipeline

# Follow multiple pods
kubectl logs -f -l app.kubernetes.io/component=scrapy -n scraping-pipeline
```

## Backup and Disaster Recovery

### Backup Delta Lake

```bash
# Snapshot PVC using your cloud provider
# AWS EBS snapshot
aws ec2 create-snapshot --volume-id vol-xxxxx --description "Delta Lake backup"

# Or use Velero for cluster-wide backups
velero backup create scraping-pipeline --include-namespaces scraping-pipeline
```

### Backup PostgreSQL

```bash
kubectl exec -it statefulset/scraping-pipeline-postgresql -n scraping-pipeline -- \
  pg_dump -U postgres scraping_pipeline > backup-$(date +%Y%m%d).sql
```

### Restore

```bash
# Restore PostgreSQL
kubectl exec -i statefulset/scraping-pipeline-postgresql -n scraping-pipeline -- \
  psql -U postgres scraping_pipeline < backup-20250101.sql
```

## Troubleshooting

### Pods Not Starting

```bash
# Check events
kubectl get events -n scraping-pipeline --sort-by='.lastTimestamp'

# Describe pod
kubectl describe pod <pod-name> -n scraping-pipeline

# Check logs
kubectl logs <pod-name> -n scraping-pipeline --previous
```

### Storage Issues

```bash
# Check PVC status
kubectl get pvc -n scraping-pipeline

# Check PV
kubectl get pv

# Describe PVC
kubectl describe pvc scraping-pipeline-delta-lake -n scraping-pipeline
```

### Kafka Connection Issues

```bash
# Test Kafka from a pod
kubectl run kafka-test --rm -it --image=confluentinc/cp-kafka:7.6.0 \
  --namespace scraping-pipeline -- \
  kafka-topics --list --bootstrap-server scraping-pipeline-kafka:9092
```

### Redis Connection Issues

```bash
# Test Redis
kubectl run redis-test --rm -it --image=redis:7-alpine \
  --namespace scraping-pipeline -- \
  redis-cli -h scraping-pipeline-redis ping
```

## Maintenance

### Update Application

```bash
# Build new image
docker build -t $REGISTRY/scrapy-app:v1.1.0 --target crawler .
docker push $REGISTRY/scrapy-app:v1.1.0

# Update values
sed -i 's/v1.0.0/v1.1.0/g' values-prod.yaml

# Rolling update
helm upgrade scraping-pipeline ./k8s/helm/scraping-pipeline -f values-prod.yaml
```

### Restart Services

```bash
# Restart scrapy (rolling)
kubectl rollout restart deployment/scraping-pipeline-scrapy -n scraping-pipeline

# Restart stateful service (careful!)
kubectl delete pod scraping-pipeline-kafka-0 -n scraping-pipeline
# StatefulSet will recreate it
```

## Production Checklist

- [ ] Secrets stored in external secret manager
- [ ] TLS certificates configured for Ingress
- [ ] Resource requests/limits set appropriately
- [ ] PodDisruptionBudgets configured
- [ ] Network policies enabled
- [ ] Monitoring alerts configured
- [ ] Backup strategy implemented
- [ ] Disaster recovery plan tested
- [ ] Log aggregation configured (ELK, Loki, etc.)
- [ ] Cost monitoring enabled
- [ ] Auto-scaling policies configured (HPA/VPA)
- [ ] Security scanning enabled (Trivy, Snyk, etc.)

## Performance Tuning

### Kafka Optimization

```yaml
kafka:
  config:
    numPartitions: 12  # Increase for higher parallelism
    defaultReplicationFactor: 3  # For production HA
    logSegmentBytes: 2147483648  # 2GB segments
```

### Scrapy Optimization

```yaml
scrapyApp:
  env:
    SCOUT_INSTANCES: "16"  # Match CPU count
  resources:
    limits:
      cpu: 16000m
```

### PostgreSQL Optimization

```yaml
postgresql:
  config:
    max_connections: 200
    shared_buffers: "4GB"
    effective_cache_size: "12GB"
    work_mem: "32MB"
```

## Support and Documentation

- Helm Chart: `/k8s/helm/scraping-pipeline/`
- Docker Compose: `/docker-compose.yml`
- Source Code: `/src/`
- Configuration: `/config/`

For issues, check:
1. Pod logs: `kubectl logs <pod>`
2. Events: `kubectl get events`
3. Metrics: Prometheus dashboard
4. Status: `helm status scraping-pipeline`
