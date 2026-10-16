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

The `*-report.txt` files are point-in-time local Trivy reports for the third-party service images (redis, kafka, zookeeper, postgres, prometheus) used by docker-compose and the Helm chart. They are reference material only, and CI does not regenerate them.

### Report dates

Trivy's text output records no scan time, so git dates these reports. All five were last changed in commit `4489059` on 2025-10-15, so each scan ran on or before that day. Treat them as a snapshot from that date, not as the current CVE state. The table compares each scanned image with the pin in the repo as of 2026-10-16.

| Report | Image scanned | Pin in the repo |
|---|---|---|
| `redis-report.txt` | `redis:7.4.2-alpine3.21` | docker-compose: `redis:7-alpine` |
| `postgres-report.txt` | `postgres:16.6-alpine3.21` | docker-compose: `postgres:16-alpine` |
| `prometheus-report.txt` | Prometheus binaries (image tag not recorded) | docker-compose: `prom/prometheus:v3.2.1` |
| `kafka-report.txt` | `confluentinc/cp-kafka:7.6.0` | Helm: `cp-kafka:7.9.1`; docker-compose: `apache/kafka:3.9.1` |
| `zookeeper-report.txt` | `confluentinc/cp-zookeeper:7.6.0` | Helm: `cp-zookeeper:7.9.1`; docker-compose has no ZooKeeper |

To refresh one locally:

```bash
trivy image --severity HIGH,CRITICAL redis:7.4.2-alpine3.21 > trivy-reports/redis-report.txt
```
