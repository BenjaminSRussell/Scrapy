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
# DRY_RUN=0 writes archives under BACKUP_DIR (required):
#   delta_lake_<BACKUP_ID>.tar.gz   contents of LAKE_SRC (required)
#   postgres_<BACKUP_ID>.sql        copy of POSTGRES_DUMP_FILE, when that is set
# LAKE_SRC, BACKUP_DIR, and POSTGRES_DUMP_FILE must sit under /tmp or this
# worktree. data/delta_lake and any other path are refused. This script never
# shells out to pg_dump. Pass an existing SQL file if the backup should
# include a database dump.
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
#   BACKUP_DIR           Archive directory. Dry-run defaults to ./backups.
#                        A real run requires a directory under /tmp or the worktree.
#   LAKE_SRC             Lake directory to archive. Required when DRY_RUN=0.
#                        Dry-run never opens the default data/delta_lake path.
#   POSTGRES_DUMP_FILE   Existing .sql file to copy. Unset skips SQL.
#   BACKUP_ID            Filename token. Default UTC YYYYMMDD_HHMMSS.
#                        Letters, digits, dot, underscore, and hyphen only.
#
# Off-box copy (#706). Unset BACKUP_S3_URI keeps the archive local and does
# not run aws, mc, or Docker. A dry-run only prints the object keys.
#   BACKUP_S3_URI        s3://bucket or s3://bucket/prefix. No credentials in
#                        the URI. The bucket is created on a real upload if
#                        it is missing.
#   BACKUP_S3_ENDPOINT   MinIO URL, for example http://127.0.0.1:9000 (the
#                        compose `backup` profile). Omit for AWS.
#   BACKUP_S3_CLIENT     aws or mc. Empty prefers aws, then mc.
#   AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are required for MinIO.
#                        They are not printed. For the compose profile, use
#                        the same values as MINIO_ROOT_USER / MINIO_ROOT_PASSWORD.
# Remote retention is a bucket lifecycle. RETENTION_DAYS and RETENTION_COUNT
# delete only files inside BACKUP_DIR. This script does not delete objects.
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "usage: $0 (set DRY_RUN, BACKUP_DIR, RETENTION_DAYS, RETENTION_COUNT, LAKE_SRC, POSTGRES_DUMP_FILE, BACKUP_ID, BACKUP_S3_URI, BACKUP_S3_ENDPOINT, BACKUP_S3_CLIENT)" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=backup_guard.sh
source "$ROOT/scripts/backup_guard.sh"
# shellcheck source=backup_s3.sh
source "$ROOT/scripts/backup_s3.sh"

DRY_RUN="${DRY_RUN:-1}"
BACKUP_DIR="${BACKUP_DIR:-}"
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

if [ -z "$BACKUP_DIR" ]; then
  if is_dry; then
    BACKUP_DIR="./backups"
  else
    echo "BACKUP_DIR is required when DRY_RUN=0" >&2
    exit 2
  fi
fi
if [ -z "$LAKE_SRC" ] && ! is_dry; then
  echo "LAKE_SRC is required when DRY_RUN=0" >&2
  exit 2
fi
BACKUP_DIR="$(anchor "$BACKUP_DIR")"
if [ -n "$LAKE_SRC" ]; then
  LAKE_SRC="$(anchor "$LAKE_SRC")"
fi
if [ -n "$POSTGRES_DUMP_FILE" ]; then
  POSTGRES_DUMP_FILE="$(anchor "$POSTGRES_DUMP_FILE")"
fi
reject_lake BACKUP_DIR "$BACKUP_DIR"
if ! is_dry || [ -L "$BACKUP_DIR" ] || [ -e "$BACKUP_DIR" ]; then
  guard_path BACKUP_DIR "$BACKUP_DIR"
fi
if ! is_dry; then
  guard_path LAKE_SRC "$LAKE_SRC"
  if [ -n "$POSTGRES_DUMP_FILE" ]; then
    guard_path POSTGRES_DUMP_FILE "$POSTGRES_DUMP_FILE"
  fi
fi
if [ -n "${BACKUP_S3_URI:-}" ]; then
  if is_dry; then
    s3_prepare_dry
  else
    s3_install_cleanup_trap
    s3_prepare_real
  fi
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
  src="$LAKE_SRC"
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

if [ -z "${BACKUP_S3_URI:-}" ]; then
  echo "s3: skipped (BACKUP_S3_URI unset)"
else
  s3_print_target
  if is_dry; then
    echo "s3_client: aws or mc (not invoked)"
    key="$(s3_object_key "delta_lake_${BACKUP_ID}.tar.gz")"
    echo "s3: would upload delta_lake_${BACKUP_ID}.tar.gz to s3://${S3_BUCKET}/${key} (not uploaded)"
    if [ -n "$POSTGRES_DUMP_FILE" ]; then
      key="$(s3_object_key "postgres_${BACKUP_ID}.sql")"
      echo "s3: would upload postgres_${BACKUP_ID}.sql to s3://${S3_BUCKET}/${key} (not uploaded)"
    fi
  else
    echo "s3_client: $S3_CLIENT"
    s3_upload_file "$BACKUP_DIR/delta_lake_${BACKUP_ID}.tar.gz" "delta_lake_${BACKUP_ID}.tar.gz"
    if [ -n "$POSTGRES_DUMP_FILE" ]; then
      s3_upload_file "$BACKUP_DIR/postgres_${BACKUP_ID}.sql" "postgres_${BACKUP_ID}.sql"
    fi
  fi
  s3_note_retention
fi

rm_in_backup_dir() {
  local p="$1" real dest base
  [ -e "$p" ] || return 0
  if [ -L "$p" ]; then
    echo "refusing to delete symlink: $p" >&2
    exit 2
  fi
  dest="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$BACKUP_DIR")" || exit 2
  real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$p")" || exit 2
  base="$(basename "$real")"
  case "$real" in
    "$dest"/*) ;;
    *)
      echo "refusing to delete outside backup dir: $real" >&2
      exit 2
      ;;
  esac
  case "$base" in
    delta_lake_*.tar.gz|postgres_*.sql) ;;
    *)
      echo "refusing to delete unexpected file: $real" >&2
      exit 2
      ;;
  esac
  rm -f "$real"
}

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
      rm_in_backup_dir "$p"
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
        rm_in_backup_dir "$f"
        echo "deleted_count: $f"
        if [ -f "$sql" ] || [ -L "$sql" ]; then
          rm_in_backup_dir "$sql"
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
