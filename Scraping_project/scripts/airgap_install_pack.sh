#!/usr/bin/env bash
# Offline air-gapped install pack (#956).
#
# Run from anywhere; the script cds to Scraping_project/.
#
#   ./scripts/airgap_install_pack.sh                  # checklist (default)
#   ./scripts/airgap_install_pack.sh checklist
#   ./scripts/airgap_install_pack.sh bundle --out /tmp/scraping-pipeline-airgap.tar
#   ./scripts/airgap_install_pack.sh bundle --out /tmp/scraping-pipeline-airgap.tar --extras
#   ./scripts/airgap_install_pack.sh load --tar /tmp/scraping-pipeline-airgap.tar
#
# checklist only prints. It does not call docker.
# bundle runs `docker save` on images that are already local. It does not
# fetch images and it does not start Compose.
# load runs `docker load` and then prints the start command. It does not
# exec that command.
#
# --out and --tar must resolve under /tmp or this worktree, and must not be
# under data/delta_lake, data/dlq, or data/kafka_spill.
# Stage 4 / ml is not in the manifest (that image installs torch).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MANIFEST="$ROOT/scripts/airgap_images.txt"
OVERLAY="$ROOT/docker-compose.airgap.yml"

START_CMD="docker compose -f docker-compose.yml -f docker-compose.airgap.yml --profile core --profile monitoring up -d --pull never --no-build"
OBS_CMD="docker compose -f docker-compose.observability.yml up -d --pull never --no-build"

usage() {
  cat <<'EOF'
usage: airgap_install_pack.sh [checklist|bundle|load]

  checklist              Print the image bundle steps (default). No docker.
  bundle --out FILE      docker save required images into FILE.
                         Add --extras to include the optional section.
  load --tar FILE        docker load FILE, then print the start command.

This script never fetches images and never starts Compose.
EOF
}

die() {
  echo "$1" >&2
  exit 2
}

[ -f "$MANIFEST" ] || die "missing manifest: $MANIFEST"
[ -f "$OVERLAY" ] || die "missing overlay: $OVERLAY"

# COLLECTED is global on purpose (bash 3.2 has no local arrays worth using).
collect() {
  local kind="$1"
  local line section="required"
  COLLECTED=()
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "# section: required") section="required"; continue ;;
      "# section: optional") section="optional"; continue ;;
      ""|\#*) continue ;;
    esac
    if [ "$section" = "$kind" ]; then
      COLLECTED+=("$line")
    fi
  done < "$MANIFEST"
}

print_names() {
  local img
  collect "$1"
  if [ "${#COLLECTED[@]}" -eq 0 ]; then
    echo "  (none)"
    return
  fi
  for img in "${COLLECTED[@]}"; do
    printf '  - %s\n' "$img"
  done
}

checklist() {
  cat <<EOF
Air-gapped install pack (#956)
No registry on the air-gapped host.

Checklist
1. On a connected host, make every required image local. This script does not fetch images.
2. Bundle: ./scripts/airgap_install_pack.sh bundle --out /tmp/scraping-pipeline-airgap.tar
3. Copy the tar, the .sha256 sidecar, and this git tree to the air-gapped host. Do not use a registry.
4. Load: ./scripts/airgap_install_pack.sh load --tar /tmp/scraping-pipeline-airgap.tar
5. Start without a registry. load prints this command and does not run it:
   $START_CMD

Image bundle steps
- Required images are saved with docker save into one tar.
- Optional images are included only with bundle --extras.
- Stage 4 and the ml target are excluded. They install torch.
- First-party tags are local names (scraping-pipeline/...:airgap), not a registry name.
- Bases python:3.11-slim, debian:bookworm-slim, and rust:1-slim-bookworm are optional extras for an offline rebuild.
- Host Python support stays 3.11 and 3.12. The image base stays python:3.11-slim.

Required images
$(print_names required)

Optional images
$(print_names optional)

No registry required path
- bundle refuses to continue when an image is not already local.
- load uses docker load only.
- docker-compose.airgap.yml sets pull_policy: never.
- The start command passes --pull never and --no-build.
- Observability, after bundle --extras: $OBS_CMD
- Redis stays REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, and REDIS_DB.
EOF
}

resolve_out() {
  local label="$1" raw="$2" real parent repo tmp root_real
  [ -n "$raw" ] || die "$label is required"
  case "$raw" in
    /*) ;;
    *) raw="$ROOT/$raw" ;;
  esac
  real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$raw")" || die "could not resolve $label"
  root_real="$(cd "$ROOT" && pwd -P)"
  repo="$(cd "$ROOT/.." && pwd -P)"
  tmp="$(cd /tmp && pwd -P)"
  case "$real" in
    "$root_real"/data/delta_lake|"$root_real"/data/delta_lake/*|\
    "$root_real"/data/dlq|"$root_real"/data/dlq/*|\
    "$root_real"/data/kafka_spill|"$root_real"/data/kafka_spill/*|\
    "$repo"/data/delta_lake|"$repo"/data/delta_lake/*)
      die "refusing $label on a data path: $real"
      ;;
  esac
  case "$real" in
    "$tmp"/*|/tmp/*|/private/tmp/*|"$root_real"/*|"$repo"/*) ;;
    *) die "refusing $label outside /tmp or the worktree: $real" ;;
  esac
  printf '%s\n' "$real"
}

require_local() {
  local img missing=0
  for img in "$@"; do
    if ! docker image inspect "$img" >/dev/null 2>&1; then
      echo "missing local image (not fetching): $img" >&2
      missing=1
    fi
  done
  if [ "$missing" -ne 0 ]; then
    echo "bundle stopped. This script does not contact a registry." >&2
    exit 1
  fi
}

write_sha() {
  local tar="$1"
  python3 - "$tar" <<'PY'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
hexdigest = digest.hexdigest()
sidecar = pathlib.Path(str(path) + ".sha256")
sidecar.write_text(hexdigest + "  " + path.name + "\n", encoding="utf-8")
print(hexdigest)
PY
}

check_sha() {
  local tar="$1" side="$tar.sha256"
  [ -f "$side" ] || return 2
  python3 - "$tar" "$side" <<'PY'
import hashlib, pathlib, sys
path = pathlib.Path(sys.argv[1])
side = pathlib.Path(sys.argv[2])
want = side.read_text(encoding="utf-8").split()[0]
digest = hashlib.sha256()
with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
got = digest.hexdigest()
if got != want:
    sys.stderr.write("sha256 mismatch for %s\n" % path.name)
    sys.exit(1)
PY
}

bundle() {
  local out="" extras=0 real parent
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --out)
        [ "$#" -ge 2 ] || die "bundle --out needs a path"
        out="$2"
        shift 2
        ;;
      --extras) extras=1; shift ;;
      *) die "unknown bundle argument: $1" ;;
    esac
  done
  [ -n "$out" ] || die "bundle requires --out FILE"
  real="$(resolve_out bundle "$out")"
  parent="$(dirname "$real")"
  [ -d "$parent" ] || die "parent directory does not exist: $parent"
  [ ! -e "$real" ] || die "refusing to overwrite $real"
  [ ! -e "$real.sha256" ] || die "refusing to overwrite $real.sha256"
  command -v docker >/dev/null 2>&1 || die "docker is not on PATH"
  collect required
  [ "${#COLLECTED[@]}" -gt 0 ] || die "required image list is empty"
  local -a imgs
  imgs=("${COLLECTED[@]}")
  if [ "$extras" -eq 1 ]; then
    collect optional
    if [ "${#COLLECTED[@]}" -gt 0 ]; then
      imgs+=("${COLLECTED[@]}")
    fi
  fi
  require_local "${imgs[@]}"
  docker save -o "$real" "${imgs[@]}"
  local hex
  hex="$(write_sha "$real")"
  echo "wrote $real"
  echo "sha256 $hex"
}

load_tar() {
  local tar="" real sha_rc
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --tar)
        [ "$#" -ge 2 ] || die "load --tar needs a path"
        tar="$2"
        shift 2
        ;;
      *) die "unknown load argument: $1" ;;
    esac
  done
  [ -n "$tar" ] || die "load requires --tar FILE"
  real="$(resolve_out load "$tar")"
  [ -f "$real" ] || die "tar does not exist: $real"
  command -v docker >/dev/null 2>&1 || die "docker is not on PATH"
  sha_rc=0
  check_sha "$real" || sha_rc=$?
  if [ "$sha_rc" -eq 1 ]; then
    die "sha256 mismatch; refusing to load"
  fi
  if [ "$sha_rc" -eq 2 ]; then
    echo "no sha256 sidecar next to the tar; load will continue" >&2
  fi
  docker load -i "$real"
  collect required
  require_local "${COLLECTED[@]}"
  cat <<EOF
loaded $real
No registry on this host.
Start command (not run by this script):
$START_CMD
Observability, only if the tar was built with --extras:
$OBS_CMD
EOF
}

cmd="${1:-checklist}"
if [ "$#" -gt 0 ]; then
  shift
fi

case "$cmd" in
  checklist|check)
    [ "$#" -eq 0 ] || die "checklist takes no arguments"
    checklist
    ;;
  bundle) bundle "$@" ;;
  load) load_tar "$@" ;;
  -h|--help|help)
    [ "$#" -eq 0 ] || die "help takes no arguments"
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
