# Terraform wrapper

One `terraform apply` installs the existing Helm chart on a cluster you already have. It does not create a VPC, a Kubernetes cluster, a load balancer, or a cloud database.

The chart and the three profiles stay the source of truth. Read [DEPLOYMENT.md](../../../DEPLOYMENT.md#helm-resource-profiles) before applying. Design notes: [943-terraform-cloud-deploy.md](../../../../docs/product/943-terraform-cloud-deploy.md). Chart install without Terraform: [k8s/README.md](../../README.md).

## What you run

From `Scraping_project/`:

```bash
cd k8s/terraform/scraping-pipeline
cp terraform.tfvars.example terraform.tfvars
# edit images, and for prod the Redis hostname, its CIDR, and the Grafana host
terraform init
terraform plan
terraform apply
```

`plan` and `apply` talk to the cluster in your kubeconfig. Do not run them from CI, and do not point them at a cluster you do not operate. `terraform destroy` uninstalls the Helm release.

Passwords are not Terraform variables. The chart default `secrets.useExternalSecrets` stays true. Create `postgres-credentials`, `grafana-credentials`, and `redis-credentials` in the release namespace first, the same way as [k8s/README.md](../../README.md). Do not set `REDIS_URL`. Clients keep `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, and `REDIS_DB`.

State is local. `terraform.tfvars` and `*.tfstate` are gitignored. Do not commit them.

## Profiles

| `profile` | Values files | When |
|---|---|---|
| `dev` | `values.yaml` | Chart defaults (Stage 1 replicaCount 3, Stage 4 off) |
| `small` (default) | `values.yaml` + `values-small.yaml` | One replica of each stage worker, HPA off |
| `prod` | `values.yaml` + `values-prod.yaml` | Production guardrails. Requires a real Redis hostname, egress CIDRs, and a Grafana hostname |

Sizes, `namespaceQuota`, and the rule that a full prod stack can exceed that quota are in [DEPLOYMENT.md](../../../DEPLOYMENT.md#helm-resource-profiles). This module does not change those numbers.

`prod` refuses the documentation placeholders `redis.example.edu`, `203.0.113.0/24`, and `grafana.example.edu`. NetworkPolicy cannot match a DNS name, so `redis_egress_cidrs` has to be the prefix of that Redis endpoint.

## Images

`crawler_image_*` is `scrapyApp`, `stage2Worker`, and `stage3Worker`. `metrics_image_*` is `metricsExporter`. `ingestor_image_*` is `kafkaDeltaIngestor`. Tags must not be `latest`. Stage 4 stays off unless `stage4_enabled` and its image are both set.

## Namespace

The default namespace and release name are `scraping-pipeline`, matching the Helm quick start. `python start.py --env k8s` uses the namespace `scraping`. Set `namespace` if you want one release, not two.

## Not in this module

- No EKS, GKE, or AKS, and no managed Redis. Prod still expects `redis.external.host` you run outside the chart.
- No second copy of the manifests. `helm_release` points at `k8s/helm/scraping-pipeline`.
- No edits to alerts, recording rules, poison-message settings, or cost caps.
- Do not set `REDIS_URL`.
