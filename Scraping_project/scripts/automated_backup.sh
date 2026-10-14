#!/usr/bin/env bash
# Automated backup for the scraping pipeline (#300).
#
# Run from Scraping_project/ (dry-run is the default):
#   ./scripts/automated_backup.sh
#   make backup
#
# A dry-run prints the plan only. It does not read data/delta_lake, does not
# write archives, does not run pg_dump or psql, and does not start Postgres,
# Redis, Kafka, or Docker.
#
# DRY_RUN=0 writes archives under BACKUP_DIR:
#   delta_lake_<BACKUP_ID>.tar.gz   contents of LAKE_SRC (default ./data/delta_lake)
#   postgres_<BACKUP_ID>.sql        copy of POSTGRES_DUMP_FILE, when that is set
# This script never shells out to pg_dump. Pass an existing SQL file if the
# backup should include a database dump.
#
# Retention (applied only when BACKUP_DIR already contains our archives;
# a dry-run lists matches and deletes nothing):
#   RETENTION_DAYS   Age cutoff in days. Default 7. Archives named
#                    delta_lake_*.tar.gz or postgres_*.sql older than this are
#                    removed on a real run. 0 disables the age cutoff.
#   RETENTION_COUNT  Keep at most this many newest delta_lake_*.tar.gz files
#                    (and the postgres_*.sql that shares the same id).
#                    Default 0 means no count cap.
#
# Other environment variables:
#   BACKUP_DIR           Archive directory. Default ./backups (under this
#                        project root). Relative paths are anchored there.
#   LAKE_SRC             Lake directory to archive. Real runs default to
#                        ./data/delta_lake. Dry-run never opens it.
#   POSTGRES_DUMP_FILE   Existing .sql file to copy. Unset skips SQL.
#   BACKUP_ID            Filename token. Default UTC YYYYMMDD_HHMMSS.
#                        Letters, digits, dot, underscore, and hyphen only.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "usage: $0 (set DRY_RUN, BACKUP_DIR, RETENTION_DAYS, RETENTION_COUNT, LAKE_SRC, POSTGRES_DUMP_FILE, BACKUP_ID)" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DRY_RUN="${DRY_RUN:-1}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
RETENTION_COUNT="${RETENTION_COUNT:-0}"
LAKE_SRC="${LAKE_SRC:-}"
POSTGRES_DUMP_FILE="${POSTGRES_DUMP_FILE:-}"
BACKUP_ID="${BACKUP_ID:-$(date -u +%Y%m%d_%H%M%S)}"

shopt -s nullglob

is_dry() {
  case "$DRY_RUN" in
    0|false|FALSE|no|NO|off|OFF) return 1 ;;
    *) return 0 ;;
  esac
}

require_count() {
  local name="$1"
  local value="$2"
  case "$value" in
    0|[1-9]|[1-9][0-9]|[1-9][0-9][0-9]|[1-9][0-9][0-9][0-9]) ;;
    *)
      echo "$name must be an integer from 0 to 9999 (got $value)" >&2
      exit 2
      ;;
  esac
}

anchor() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s\n' "$ROOT/$1" ;;
  esac
}

require_count RETENTION_DAYS "$RETENTION_DAYS"
require_count RETENTION_COUNT "$RETENTION_COUNT"

case "$BACKUP_ID" in
  *[!A-Za-z0-9_.-]*)
    echo "BACKUP_ID must contain only letters, digits, '.', '_' and '-'" >&2
    exit 2
    ;;
esac

BACKUP_DIR="$(anchor "$BACKUP_DIR")"
if [ -n "$LAKE_SRC" ]; then
  LAKE_SRC="$(anchor "$LAKE_SRC")"
fi
if [ -n "$POSTGRES_DUMP_FILE" ]; then
  POSTGRES_DUMP_FILE="$(anchor "$POSTGRES_DUMP_FILE")"
fi

default_lake="$ROOT/data/delta_lake"

echo "mode: $(if is_dry; then printf 'dry-run'; else printf 'write'; fi)"
echo "backup_id: $BACKUP_ID"
echo "backup_dir: $BACKUP_DIR"
echo "retention_days: $RETENTION_DAYS"
echo "retention_count: $RETENTION_COUNT"

if is_dry; then
  if [ -n "$LAKE_SRC" ]; then
    echo "lake: $LAKE_SRC (not read; dry-run)"
  else
    echo "lake: $default_lake (not read; dry-run)"
  fi
  if [ -n "$POSTGRES_DUMP_FILE" ]; then
    echo "postgres: would copy $POSTGRES_DUMP_FILE (not copied; dry-run; pg_dump is not run)"
  else
    echo "postgres: skipped (POSTGRES_DUMP_FILE unset; pg_dump is not run)"
  fi
else
  src="${LAKE_SRC:-$default_lake}"
  if [ ! -d "$src" ]; then
    echo "lake source not found: $src" >&2
    exit 1
  fi
  mkdir -p "$BACKUP_DIR"
  # Archive the lake's contents so restore extracts straight into RESTORE_DEST.
  COPYFILE_DISABLE=1 tar -czf "$BACKUP_DIR/delta_lake_${BACKUP_ID}.tar.gz" -C "$src" .
  echo "wrote: $BACKUP_DIR/delta_lake_${BACKUP_ID}.tar.gz"
  if [ -n "$POSTGRES_DUMP_FILE" ]; then
    if [ ! -f "$POSTGRES_DUMP_FILE" ]; then
      echo "POSTGRES_DUMP_FILE not found: $POSTGRES_DUMP_FILE" >&2
      exit 1
    fi
    cp "$POSTGRES_DUMP_FILE" "$BACKUP_DIR/postgres_${BACKUP_ID}.sql"
    echo "wrote: $BACKUP_DIR/postgres_${BACKUP_ID}.sql"
  else
    echo "postgres: skipped (POSTGRES_DUMP_FILE unset; pg_dump is not run)"
  fi
fi

list_tars() {
  local f
  local items
  items=()
  for f in "$BACKUP_DIR"/delta_lake_*.tar.gz; do
    items+=("$f")
  done
  if [ "${#items[@]}" -eq 0 ]; then
    return 0
  fi
  ls -1t "${items[@]}"
}

apply_age_retention() {
  if [ "$RETENTION_DAYS" -eq 0 ] || [ ! -d "$BACKUP_DIR" ]; then
    return 0
  fi
  local listing p
  listing="$(find "$BACKUP_DIR" -maxdepth 1 -type f \
    \( -name 'delta_lake_*.tar.gz' -o -name 'postgres_*.sql' \) \
    -mtime +"$RETENTION_DAYS" -print || true)"
  [ -n "$listing" ] || return 0
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    if is_dry; then
      echo "would_delete_age: $p"
    else
      rm -f "$p"
      echo "deleted_age: $p"
    fi
  done <<EOF
$listing
EOF
}

apply_count_retention() {
  if [ "$RETENTION_COUNT" -eq 0 ] || [ ! -d "$BACKUP_DIR" ]; then
    return 0
  fi
  local lines f i id sql
  lines="$(list_tars || true)"
  [ -n "$lines" ] || return 0
  i=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ "$i" -ge "$RETENTION_COUNT" ]; then
      id="$(basename "$f")"
      id="${id#delta_lake_}"
      id="${id%.tar.gz}"
      sql="$BACKUP_DIR/postgres_${id}.sql"
      if is_dry; then
        echo "would_delete_count: $f"
        if [ -f "$sql" ]; then
          echo "would_delete_count: $sql"
        fi
      else
        rm -f "$f"
        echo "deleted_count: $f"
        if [ -f "$sql" ]; then
          rm -f "$sql"
          echo "deleted_count: $sql"
        fi
      fi
    fi
    i=$((i + 1))
  done <<EOF
$lines
EOF
}

if [ ! -d "$BACKUP_DIR" ]; then
  echo "retention: backup dir absent; nothing to prune"
else
  apply_age_retention
  apply_count_retention
fi

echo "done"
