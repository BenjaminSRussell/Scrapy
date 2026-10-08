# Container vulnerability scanning (#499)

## CI gate

`.github/workflows/cd-release.yml` runs Trivy (`aquasecurity/trivy-action`, pinned to a commit SHA) on each image it builds: `crawler`, `metrics` and `kafka-delta-ingest`. The scan runs right after the image is built and smoke-tested, and **before** anything is pushed.

| Setting | Value | Meaning |
|---|---|---|
| `severity` | `CRITICAL` | Only critical findings can fail the job |
| `ignore-unfixed` | `true` | A CVE fails the job only if a fixed version exists (otherwise nothing in this repo can act on it) |
| `exit-code` | `1` | Fixable CRITICAL findings **fail** the PR or release |
| `trivyignores` | `Scraping_project/.trivyignore` | Reviewed suppressions |

The gate runs on:

- every pull request that touches `Dockerfile`, `.dockerignore`, `docker/**`, `kafka-delta-ingest/**`, `requirements.txt`, `.trivyignore` or the workflow itself;
- every `v*` release tag;
- manual `workflow_dispatch` runs.

## Fixing a failure

Work through these in order:

1. Bump the vulnerable package (`requirements*.in`, then `make lock`) or the base image tag in `Dockerfile`.
2. If no fix applies to how we use the package, add the CVE to `.trivyignore` under a comment naming the image or package and the reason. Prefer a bump over a suppression.
3. When a base image is bumped, review the whole `.trivyignore` list and delete stale entries.

## This directory

The `*-report.txt` files are point-in-time local Trivy reports for the third-party service images (redis, kafka, zookeeper, postgres, prometheus) used by docker-compose. They are reference material only, and CI does not regenerate them.

To refresh one locally:

```bash
trivy image --severity HIGH,CRITICAL redis:7.4.2-alpine3.21 > trivy-reports/redis-report.txt
```
