# Terraform module for one-click cloud deploy

- **Status:** MVP. The wrapper is HCL for an existing cluster. It does not provision cloud accounts.
- **Date:** 2026-10-14
- **Issue:** [#943](https://github.com/BenjaminSRussell/Scrapy/issues/943)

Ops growth past a hand-run Helm install. The supported Kubernetes install is still the chart at `Scraping_project/k8s/helm/scraping-pipeline`. This design does not replace it.

Deployment steps, the dev / small / prod resource table, and `namespaceQuota` stay in the [deployment guide](../../Scraping_project/DEPLOYMENT.md#helm-resource-profiles). The module README is the apply command: [k8s/terraform/scraping-pipeline](../../Scraping_project/k8s/terraform/scraping-pipeline/README.md).

## Decision

One-click means one `terraform apply` of a root module that wraps that chart. The operator already has a kubeconfig. The module picks `dev`, `small`, or `prod` by passing the same values files Helm already uses, then sets images and the prod Redis and Grafana hostnames.

We will not keep a second set of Kubernetes manifests in Terraform, and we will not create VPCs, node groups, or managed databases in this module. Those are a later cloud stack that would call this wrapper. They are not required to install the pipeline, and they are not free to apply by accident.

`small` is the default profile. `dev` is the chart's replica counts. `prod` is the durability overlay and must be chosen on purpose.

## What the module sets

| Input | Helm keys | Notes |
|---|---|---|
| `profile=dev` | `values.yaml` | No overlay |
| `profile=small` | `values.yaml` then `values-small.yaml` | One replica, HPA off |
| `profile=prod` | `values.yaml` then `values-prod.yaml` | `profile: production` guardrails stay in the overlay |
| crawler image | `scrapyApp`, `stage2Worker`, `stage3Worker` | Same repository and tag |
| metrics image | `metricsExporter` | Dockerfile `metrics` target |
| ingestor image | `kafkaDeltaIngestor` | Dockerfile `kafka-delta-ingest` target |
| `stage4_enabled` | `stage4Worker.enabled` plus its image | Default off, matching the chart |
| `redis_host` / `redis_port` | `redis.enabled=false`, `redis.external.host`, `redis.external.port` | Hostname only. Required for prod |
| `redis_egress_cidrs` | `redis.external.egressCIDRs` | Required for prod. Not a DNS name |
| `grafana_host` | `ingress.hosts[0].host` and, on prod, `ingress.tls[0].hosts[0]` | Required for prod |
| storage classes | `global.storageClass`, `deltaLake.persistence.storageClass` | Delta needs ReadWriteMany |

Release name and namespace default to `scraping-pipeline` ([k8s README](../../Scraping_project/k8s/README.md)). `python start.py --env k8s` uses namespace `scraping`. Pick one namespace. Do not install both by accident.

Secrets stay outside Terraform. `secrets.useExternalSecrets` stays the chart default (true). `REDIS_PASSWORD` stays the `redis-credentials` Secret. There is no password variable and no `REDIS_URL` value. `REDIS_DB` stays the process default unless the chart sets it.

`prod` fails the plan when `redis_host` is empty or `redis.example.edu`, when the egress list is empty or still `203.0.113.0/24`, or when `grafana_host` is empty or `grafana.example.edu`. That is the same placeholder rule as [values-prod.yaml](../../Scraping_project/k8s/helm/scraping-pipeline/values-prod.yaml). A full prod stack can still exceed `namespaceQuota` in `values.yaml`. Raise that quota or turn components off in the chart before apply. This module does not change the quota.

## Left unchanged

- Alert rules, recording rules, and `pipeline_slos.json`. The three SLOs stay `Stage2SuccessSLO` with no bool modifier, `QueueBacklog` above 10000, and `KafkaConsumerLagSLO` above 250000 messages.
- Poison-message policy (`dlqTopic`, `maxDeserializeFailures`) on the ingestor.
- Null cost caps. This module does not set one.
- Chart Redis for `dev` and `small` when `redis_host` is empty: one replica with AOF. AOF can recover data after the node returns, and it does not keep the queue available during node loss. Prod still points at an external hostname.

## Cloud shape (not built here)

| Cloud | Cluster you bring | Delta Lake (RWX) | Redis for prod |
|---|---|---|---|
| AWS | EKS kubeconfig | EFS storage class | Hostname plus its CIDR (ElastiCache primary or a name you already run) |
| GCP | GKE kubeconfig | Filestore class | Memorystore or another external primary, as a hostname plus CIDR |
| Azure | AKS kubeconfig | Azure Files class | Azure Cache or another external primary, as a hostname plus CIDR |

Creating those accounts is a follow-up. It does not belong in the wrapper, because `terraform apply` would then be a bill, not an install of this chart.

## Alternatives

- **Helm only.** Already shipped. It is not one command once images, profile, Redis, and the Grafana host are involved, and it is easy to skip a values file.
- **Terraform resources for every manifest.** Duplicates the chart and will drift from `values-prod.yaml` guardrails.
- **EKS/GKE/AKS module in this change.** One click would create paid infrastructure. The issue allows a docs MVP. The HCL here is the install wrapper only.
