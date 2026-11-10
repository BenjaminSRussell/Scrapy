#!/usr/bin/env bash
# Cross-region Delta replication stub (#705).
#
# Dry-run only. Prints the rclone sync an operator would run against the
# Delta lake root. Does not call rclone, does not read data/delta_lake,
# does not copy files, and does not start Docker, Kubernetes, or a remote.
#
#   ./scripts/delta_replicate.sh
#   make delta-replicate
#
# Environment (all optional):
#   LAKE_SRC       Lake root printed in the plan. Unset prints the config
#                  default ./data/delta_lake and does not open it.
#   RCLONE_DEST    Destination remote. Default dr:delta-lake, a placeholder
#                  that is not contacted. Use a name from `rclone config`.
#                  Do not embed credentials.
#   RCLONE_FLAGS   Extra flags included in the printed command. Not executed.
#   DRY_RUN        Must stay on (1/true/yes/on). Any other value is refused.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "usage: $0 (set DRY_RUN, LAKE_SRC, RCLONE_DEST, RCLONE_FLAGS)" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DRY_RUN="${DRY_RUN:-1}"
LAKE_SRC="${LAKE_SRC:-}"
RCLONE_DEST="${RCLONE_DEST:-dr:delta-lake}"
RCLONE_FLAGS="${RCLONE_FLAGS:-}"

reject_oneline() {
  local name="$1" value="$2"
  case "$value" in
    *$'\n'*|*$'\r'*)
      echo "$name must be a single line" >&2
      exit 2
      ;;
  esac
}

reject_secret() {
  local name="$1" value="$2"
  case "$value" in
    *://*:*@*)
      echo "refusing $name with embedded credentials; configure an rclone remote instead" >&2
      exit 2
      ;;
  esac
}

reject_oneline DRY_RUN "$DRY_RUN"
reject_oneline LAKE_SRC "$LAKE_SRC"
reject_oneline RCLONE_DEST "$RCLONE_DEST"
reject_oneline RCLONE_FLAGS "$RCLONE_FLAGS"
reject_secret LAKE_SRC "$LAKE_SRC"
reject_secret RCLONE_DEST "$RCLONE_DEST"
reject_secret RCLONE_FLAGS "$RCLONE_FLAGS"

case "$DRY_RUN" in
  1|true|TRUE|yes|YES|on|ON) ;;
  *)
    echo "refusing DRY_RUN=$DRY_RUN: delta-replicate is a dry-run stub and does not copy the lake (#705)" >&2
    exit 2
    ;;
esac

case "$RCLONE_DEST" in
  -*)
    echo "RCLONE_DEST must not start with '-'" >&2
    exit 2
    ;;
esac

src="./data/delta_lake"
src_note="config default; not read"
if [ -n "$LAKE_SRC" ]; then
  case "$LAKE_SRC" in
    -*)
      echo "LAKE_SRC must not start with '-'" >&2
      exit 2
      ;;
  esac
  src="$LAKE_SRC"
  src_note="not read; dry-run"
fi

echo "mode: dry-run"
echo "lake: $src ($src_note)"
echo "lake_compose: /data/delta (container DELTA_LAKE_PATH; host ./data/delta; not read)"
echo "lake_helm: /app/data/delta_lake (PVC *-delta-lake mounted at /app/data; not read)"
echo "dest: $RCLONE_DEST (not contacted)"
echo "warning: concurrent writers: stop scrapy, stage workers, LakehouseManager, and kafka-delta-ingest and let the Delta write queue drain before a real sync. A copy taken while a commit is in flight can pair a new _delta_log entry with missing data files, or the reverse. rclone sync also propagates vacuum deletions. This stub does not check writers, does not call rclone, and does not copy."
if [ -n "$RCLONE_FLAGS" ]; then
  printf 'command: rclone sync --dry-run %s %s %s\n' "$RCLONE_FLAGS" "$src" "$RCLONE_DEST"
else
  printf 'command: rclone sync --dry-run %s %s\n' "$src" "$RCLONE_DEST"
fi
echo "done"
