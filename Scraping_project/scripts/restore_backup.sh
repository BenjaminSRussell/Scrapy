#!/usr/bin/env bash
# Restore a backup written by automated_backup.sh (#300).
#
# Run from Scraping_project/ (dry-run is the default):
#   ./scripts/restore_backup.sh
#   make restore
#
# A dry-run prints the archive it would extract. It does not create
# RESTORE_DEST, does not read data/delta_lake, and does not contact a database.
#
# DRY_RUN=0 extracts delta_lake_<BACKUP_ID>.tar.gz (default: newest) into
# RESTORE_DEST. RESTORE_DEST is required on a real run and must be a directory
# under /tmp or this worktree. data/delta_lake and any other path are refused.
# A matching postgres_<id>.sql is copied to RESTORE_SQL_DEST (default: sibling
# of RESTORE_DEST). The SQL file is never loaded with psql, and pg_dump is never
# run.
#
# ROUNDTRIP=1 ignores BACKUP_DIR and the project tree. It builds a sample lake
# and SQL file under /tmp, backs them up, restores them, and compares bytes.
#
# Retention is not applied here. See automated_backup.sh for:
#   RETENTION_DAYS (default 7) and RETENTION_COUNT (default 0, no cap).
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "usage: $0 (set DRY_RUN, BACKUP_DIR, BACKUP_ID, RESTORE_DEST, RESTORE_SQL_DEST, ROUNDTRIP)" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=backup_guard.sh
source "$ROOT/scripts/backup_guard.sh"

DRY_RUN="${DRY_RUN:-1}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_ID="${BACKUP_ID:-}"
RESTORE_DEST="${RESTORE_DEST:-}"
RESTORE_SQL_DEST="${RESTORE_SQL_DEST:-}"
ROUNDTRIP="${ROUNDTRIP:-0}"

shopt -s nullglob

is_dry() {
  case "$DRY_RUN" in
    0|false|FALSE|no|NO|off|OFF) return 1 ;;
    *) return 0 ;;
  esac
}

anchor() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s\n' "$ROOT/$1" ;;
  esac
}

assert_safe_members() {
  local archive="$1"
  local listing entry rest seg
  listing="$(tar -tzf "$archive")"
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    case "$entry" in
      /*)
        echo "refusing absolute path in archive: $entry" >&2
        exit 1
        ;;
    esac
    rest="$entry"
    while [ -n "$rest" ]; do
      seg="${rest%%/*}"
      if [ "$seg" = ".." ]; then
        echo "refusing parent path in archive: $entry" >&2
        exit 1
      fi
      if [ "$rest" = "$seg" ]; then
        break
      fi
      rest="${rest#*/}"
    done
  done <<EOF
$listing
EOF
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

run_roundtrip() {
  local work src backup dest sql restored_sql marker got
  work="$(mktemp -d /tmp/scrapy-backup-roundtrip.XXXXXX)"
  trap 'rm -rf "$work"' EXIT
  src="$work/src/delta_lake"
  mkdir -p "$src/sample_table"
  printf 'roundtrip-marker\n' > "$src/sample_table/part.txt"
  printf 'SELECT 1;\n' > "$work/src/sample.sql"
  backup="$work/backups"
  dest="$work/restored/delta_lake"
  restored_sql="$work/restored/sample.sql"
  DRY_RUN=0 \
    BACKUP_DIR="$backup" \
    LAKE_SRC="$src" \
    POSTGRES_DUMP_FILE="$work/src/sample.sql" \
    BACKUP_ID="roundtrip" \
    RETENTION_DAYS=7 \
    RETENTION_COUNT=0 \
    "$ROOT/scripts/automated_backup.sh"
  DRY_RUN=0 \
    BACKUP_DIR="$backup" \
    BACKUP_ID="roundtrip" \
    RESTORE_DEST="$dest" \
    RESTORE_SQL_DEST="$restored_sql" \
    ROUNDTRIP=0 \
    "$ROOT/scripts/restore_backup.sh"
  marker="$(cat "$dest/sample_table/part.txt")"
  if [ "$marker" != "roundtrip-marker" ]; then
    echo "roundtrip lake mismatch" >&2
    exit 1
  fi
  got="$(cat "$restored_sql")"
  if [ "$got" != "SELECT 1;" ]; then
    echo "roundtrip sql mismatch" >&2
    exit 1
  fi
  if [ ! -f "$backup/delta_lake_roundtrip.tar.gz" ] || [ ! -f "$backup/postgres_roundtrip.sql" ]; then
    echo "roundtrip archives missing" >&2
    exit 1
  fi
  echo "roundtrip: ok"
  exit 0
}

if [ "$ROUNDTRIP" = "1" ]; then
  run_roundtrip
fi

BACKUP_DIR="$(anchor "$BACKUP_DIR")"
reject_lake BACKUP_DIR "$BACKUP_DIR"
if ! is_dry || [ -L "$BACKUP_DIR" ] || [ -e "$BACKUP_DIR" ]; then
  guard_path BACKUP_DIR "$BACKUP_DIR"
fi

archive=""
sql=""
chosen_id=""
if [ -n "$BACKUP_ID" ]; then
  case "$BACKUP_ID" in
    *[!A-Za-z0-9_.-]*)
      echo "BACKUP_ID must contain only letters, digits, '.', '_' and '-'" >&2
      exit 2
      ;;
  esac
  chosen_id="$BACKUP_ID"
  archive="$BACKUP_DIR/delta_lake_${BACKUP_ID}.tar.gz"
  sql="$BACKUP_DIR/postgres_${BACKUP_ID}.sql"
else
  lines="$(list_tars || true)"
  if [ -n "$lines" ]; then
    archive="${lines%%$'\n'*}"
    chosen_id="$(basename "$archive")"
    chosen_id="${chosen_id#delta_lake_}"
    chosen_id="${chosen_id%.tar.gz}"
    sql="$BACKUP_DIR/postgres_${chosen_id}.sql"
  fi
fi

if is_dry; then
  echo "mode: dry-run"
  echo "backup_dir: $BACKUP_DIR"
  if [ -z "$archive" ] || [ ! -f "$archive" ]; then
    echo "archive: none"
    echo "restore: nothing to restore"
    echo "done"
    exit 0
  fi
  if [ -n "$RESTORE_DEST" ]; then
    echo "restore_dest: $(anchor "$RESTORE_DEST") (not written)"
  else
    echo "restore_dest: required when DRY_RUN=0 (not written)"
  fi
  echo "archive: $archive (not extracted)"
  if [ -f "$sql" ]; then
    echo "postgres: $sql (not copied; psql is not run)"
  else
    echo "postgres: no matching sql (psql is not run)"
  fi
  echo "done"
  exit 0
fi

if [ -z "$RESTORE_DEST" ]; then
  echo "RESTORE_DEST is required when DRY_RUN=0" >&2
  exit 2
fi
RESTORE_DEST="$(anchor "$RESTORE_DEST")"
guard_path RESTORE_DEST "$RESTORE_DEST"
if [ -z "$archive" ] || [ ! -f "$archive" ]; then
  echo "archive not found: ${archive:-$BACKUP_DIR/delta_lake_*.tar.gz}" >&2
  exit 1
fi

assert_safe_members "$archive"
mkdir -p "$RESTORE_DEST"
COPYFILE_DISABLE=1 tar -xzf "$archive" -C "$RESTORE_DEST"
echo "restored: $archive -> $RESTORE_DEST"

if [ -f "$sql" ]; then
  if [ -z "$RESTORE_SQL_DEST" ]; then
    RESTORE_SQL_DEST="$(dirname "$RESTORE_DEST")/postgres_${chosen_id}.sql"
  else
    RESTORE_SQL_DEST="$(anchor "$RESTORE_SQL_DEST")"
  fi
  guard_path RESTORE_SQL_DEST "$RESTORE_SQL_DEST"
  mkdir -p "$(dirname "$RESTORE_SQL_DEST")"
  cp "$sql" "$RESTORE_SQL_DEST"
  echo "copied_sql: $sql -> $RESTORE_SQL_DEST (not loaded)"
else
  echo "postgres: no matching sql (psql is not run)"
fi
echo "done"
