# Offline air-gapped install pack

Operators on a network that cannot reach a container registry install the
pipeline from a tar of images. The canonical name list is
[`scripts/airgap_images.txt`](../../scripts/airgap_images.txt).
[`scripts/airgap_install_pack.sh`](../../scripts/airgap_install_pack.sh)
prints the checklist, writes the tar, and loads it. Nothing in that script
fetches an image or starts Compose.

Run every command from `Scraping_project/`.

Stage 4 and the `ml` image are not in the pack. Those targets install torch.
Do not build `--target ml` or `--target stage4` for this pack.

Redis stays `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, and `REDIS_DB`.
Do not replace those four variables with one Redis URL. Host Python support
stays 3.11 and 3.12. The image base stays `python:3.11-slim`.

## Image bundle steps

Do this on a connected host. That host may obtain images. The air-gapped
host in the next section must not.

1. Build the two local tags the default pack requires. These names are not a
   registry. Do not `docker login` and do not push them.

   ```bash
   cd Scraping_project
   docker build --target crawler -t scraping-pipeline/crawler:airgap .
   docker build --target stage3 -t scraping-pipeline/stage3:airgap .
   ```

   `crawler` is the same contents as `production` (core lock, no torch).
   Compose `command:` still selects stage 1, stage 2, and the ops dashboard.
   Stage 3 uses its own tag.

2. Make every other **required** line in `scripts/airgap_images.txt` local.
   The pinned third-party tags are the `image:` values on the core and
   monitoring profiles in `docker-compose.yml` (`redis:7-alpine`,
   `postgres:16-alpine`, the exporters, Prometheus, and Grafana). Obtain
   those tags on this connected host only. The pack script will not do it
   for you.

3. Optional extras (streaming Kafka, the observability overlay, release
   images, and the Dockerfile bases `python:3.11-slim`,
   `debian:bookworm-slim`, `rust:1-slim-bookworm`) are the `# section: optional`
   lines. Build the extra local tags only when you need them:

   ```bash
   docker build --target metrics -t scraping-pipeline/metrics:airgap .
   docker build --target kafka-delta-ingest -t scraping-pipeline/kafka-delta-ingest:airgap .
   ```

   Skip both when the air-gapped host only runs Compose core and monitoring.

4. Save the images with `docker save` (one tar). `--out` must be under
   `/tmp` or this worktree, and not under `data/delta_lake`, `data/dlq`, or
   `data/kafka_spill`. The script exits if any selected image is not already
   local. It never pulls.

   ```bash
   ./scripts/airgap_install_pack.sh bundle --out /tmp/scraping-pipeline-airgap.tar
   ./scripts/airgap_install_pack.sh bundle --out /tmp/scraping-pipeline-airgap.tar --extras
   ```

   `bundle` writes the tar and a sidecar
   `/tmp/scraping-pipeline-airgap.tar.sha256` (`sha256sum` layout: hash, two
   spaces, file name).

5. Copy the tar, the sidecar, and this git tree to the air-gapped host on
   removable media or another path that is not a registry.

## Script checklist

`./scripts/airgap_install_pack.sh` with no arguments prints the checklist and
exits. It does not call Docker. The same text is `checklist`.

| Step | What to check |
|---|---|
| 1 | Required images from the manifest are local on the connected host |
| 2 | `bundle --out` produced a tar and a `.sha256` sidecar |
| 3 | The tar, sidecar, and git tree are on the air-gapped host |
| 4 | `load --tar` reported each required image present |
| 5 | The start command below was used as printed, with `--pull never` and `--no-build` |

`bundle` and `load` refuse a path outside `/tmp` or the worktree. They refuse
to overwrite an existing tar.

## No registry required path

On the air-gapped host:

```bash
cd /tmp
shasum -a 256 -c scraping-pipeline-airgap.tar.sha256
cd Scraping_project
./scripts/airgap_install_pack.sh load --tar /tmp/scraping-pipeline-airgap.tar
```

`load` runs `docker load` only. If the sidecar is present and does not match,
it stops before the load. It then checks that every required image is local.
It prints the start command and does not run it.

`docker-compose.airgap.yml` sets `pull_policy: never` on every service in
`docker-compose.yml` except `stage4-worker`, which is left out of the
overlay. Built services point at `scraping-pipeline/crawler:airgap` or
`scraping-pipeline/stage3:airgap`. Third-party services keep the tags already
pinned in `docker-compose.yml`.

```bash
docker compose -f docker-compose.yml -f docker-compose.airgap.yml \
  --profile core --profile monitoring up -d --pull never --no-build
```

`--pull never` will not contact a registry. `--no-build` will not rebuild
(a rebuild would need apt and pip). If an image is missing, Compose stops.
That is the failure you want on an air-gapped host. Do not drop either flag.
Do not pass `--profile ml` or `--profile full`.

Kafka is optional. Include it only after `bundle --extras`, then add
`--profile streaming` to the command above. The observability file is the
same rule, and only after extras:

```bash
docker compose -f docker-compose.observability.yml up -d --pull never --no-build
```

### Kubernetes

The chart does not change. On a node that already has the loaded images, set
the local tag and `pullPolicy: Never` for the workloads you run (for example
`scrapyApp` and `stage2Worker` / `stage3Worker` use
`scraping-pipeline/crawler:airgap`; the metrics exporter uses
`scraping-pipeline/metrics:airgap`). Do not point those fields at `ghcr.io`
for this path. An empty repository in the chart stays empty until an operator
sets it. There is still no in-cluster registry in this pack.

Release tags published by `cd-release.yml` are for connected environments.
This pack does not use them.
