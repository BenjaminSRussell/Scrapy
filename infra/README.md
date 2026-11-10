# Cloud infrastructure stub

Helm (`Scraping_project/k8s/`) and Compose remain the supported installs.
This directory is a Terraform stub for the cloud pieces those installs do not
create: a VPC, an object-store bucket, and Redis. Nothing here is applied by
CI, and this repository does not ship credentials.

Decision record: [ADR-0004](../docs/adr/0004-terraform-stub-not-pulumi.md).

## What is declared

| Piece | Resource | Notes |
| --- | --- | --- |
| VPC | `aws_vpc` plus two private subnets | No internet gateway and no NAT. Not an EKS cluster. |
| Bucket | `aws_s3_bucket` `lake` | Versioning, AES256, public access blocked, bucket-owner enforced. Default name `example-scraping-delta-lake` is a placeholder. |
| MinIO | Not a resource | Same `lake_bucket_name`. Optional `minio_endpoint` is a host[:port] only. No access key argument exists. |
| Redis | `aws_elasticache_replication_group` | One node (`num_cache_clusters = 1`), engine 7.4, port 6379, `maxmemory-policy=volatile-lru`, snapshot retention 0. Not HA. |

The running lake is still a filesystem: `DELTA_LAKE_PATH`, or the Helm delta
PVC. This bucket does not replace that path until an operator wires a client,
which this stub does not do.

Production Helm already expects Redis outside the chart (`redis.enabled: false`,
`redis.external.host` / `port` in `values-prod.yaml`). Point those at
`REDIS_HOST` and `REDIS_PORT` from the `redis_env` output if you ever apply.
`REDIS_PASSWORD` is `TF_VAR_redis_auth_token` and is not an output. `REDIS_DB`
stays `0`. Do not set `REDIS_URL`.

One node does not keep the queue available during node loss, same as the chart
Redis (#216). Seen-sets and queues stay on `volatile-lru` so a full instance
rejects writes instead of evicting them.

GCP (GCS and Memorystore) is not in this stub. Add it in a later change if a
real account exists; do not invent a second provider here.

No AWS budget is declared. Cost stays uncapped until a human adds one.

## Safety

- `allow_apply` defaults to **false**. `terraform plan` fails until a human
  sets it true and supplies `TF_VAR_redis_auth_token` (16–128 characters).
  `terraform validate` does not check that latch, so CI can validate with no
  token and no AWS credentials.
- Do not run `terraform apply` or `terraform plan` from CI or against a real
  account as part of this repository's automation.
- Do not commit `terraform.tfvars`, state, or a token. `.gitignore` ignores
  `*.tfvars` (except `*.tfvars.example`), `*.tfstate`, and `.terraform/`.
- The AWS provider block has no static credentials. A real apply would use
  the operator's own environment or role, outside this repo.

## Commands

From `infra/terraform/`, with Terraform >= 1.5 (CI pins 1.16.6):

```bash
terraform fmt -check -diff -recursive
terraform init -backend=false -input=false
terraform validate
```

`init` downloads the AWS provider plugin. It does not contact AWS. The backend
is local; do not switch it to a remote backend in this repository.

## CI

[`.github/workflows/ci-terraform.yml`](../.github/workflows/ci-terraform.yml)
runs those three commands on pull requests (and on pushes to `main`) that touch
`infra/**` or the workflow file. It does not run for other PRs, and it does
not plan or apply. It is not a deploy.
