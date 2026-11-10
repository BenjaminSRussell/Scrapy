# ADR-0004: Cloud stub is Terraform, not Pulumi

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #693

## Context

Production installs are Helm and Compose. Helm's production overlay turns the
in-cluster Redis off and reads `REDIS_HOST` / `REDIS_PORT` (password stays
`REDIS_PASSWORD`; do not set `REDIS_URL`). The Delta lake is a filesystem path
or a PVC, not an object store. There is no cloud account in this repository
and no checked-in credentials. Issue #693 asks for a Terraform or Pulumi stub
so a VPC, a bucket, and Redis can be repeated later.

## Decision

We will keep a Terraform stub under `infra/terraform/` and will not add Pulumi.

1. The stub declares an AWS VPC (private subnets only), an S3 bucket, and a
   single-node ElastiCache Redis with `maxmemory-policy=volatile-lru`. MinIO is
   a documented S3-compatible endpoint and bucket name, not a provider.
2. GCP is out of scope until a later change. One cloud keeps the stub reviewable.
3. `allow_apply` defaults to false. CI runs `terraform fmt` and `terraform validate`
   only when `infra/**` changes. CI does not run `terraform plan` or `terraform apply`.
4. No static credentials, no `REDIS_URL`, and no AWS budget. Cost stays uncapped
   until a human adds a budget.
5. Helm and Compose stay the supported way to run the pipeline. The stub does
   not replace them.

## Consequences

- Operators can read one directory for the intended cloud shape without applying it.
- A real apply is a later, human step and needs a token supplied outside git.
- Pulumi, GCS, and Memorystore are not generated. Adding them means a new ADR
  if they replace this stub rather than sit beside it.
- The stub can drift from Helm values (Redis engine 7.4, node size, bucket name).
  Those values are placeholders, not a capacity decision.

## Alternatives considered

- **Pulumi:** same resources in a general-purpose language, plus a second toolchain
  and a second CI install. The issue's path is `infra/terraform/`. Not worth a
  second tool for a stub that must not apply.
- **Apply from CI:** would need a real account, state, and credentials. Rejected.
- **GCP-only or dual-cloud module:** no account to validate against, and it doubles
  the stub. Rejected for this change.
