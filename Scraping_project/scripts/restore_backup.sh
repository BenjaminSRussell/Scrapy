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
# Remote objects are not deleted either. Set a bucket lifecycle (#706).
#
# BACKUP_S3_URI, when set, is the same s3://bucket/prefix used by the backup.
# A dry-run does not download. A real run downloads delta_lake_<BACKUP_ID>.tar.gz
# into BACKUP_DIR only when that file is not already there, then extracts it.
# BACKUP_ID is required for that download. A matching postgres_<id>.sql object
# is copied when it exists and is still not loaded with psql. Credentials are
# AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY. MinIO also needs
# BACKUP_S3_ENDPOINT (compose profile `backup`: http://127.0.0.1:9000).
set -euo pipefail

if [ "$#" -ne 0 ]; then
  echo "usage: $0 (set DRY_RUN, BACKUP_DIR, BACKUP_ID, RESTORE_DEST, RESTORE_SQL_DEST, ROUNDTRIP, BACKUP_S3_URI, BACKUP_S3_ENDPOINT, BACKUP_S3_CLIENT)" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=backup_guard.sh
source "$ROOT/scripts/backup_guard.sh"
# shellcheck source=backup_s3.sh
source "$ROOT/scripts/backup_s3.sh"

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
    BACKUP_S3_URI="" \
    BACKUP_S3_ENDPOINT="" \
    BACKUP_S3_CLIENT="" \
    "$ROOT/scripts/automated_backup.sh"
  DRY_RUN=0 \
    BACKUP_DIR="$backup" \
    BACKUP_ID="roundtrip" \
    RESTORE_DEST="$dest" \
    RESTORE_SQL_DEST="$restored_sql" \
    ROUNDTRIP=0 \
    BACKUP_S3_URI="" \
    BACKUP_S3_ENDPOINT="" \
    BACKUP_S3_CLIENT="" \
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

if [ -n "${BACKUP_S3_URI:-}" ]; then
  s3_prepare_dry
  s3_print_target
  if is_dry; then
    echo "s3_client: aws or mc (not invoked)"
  fi
else
  echo "s3: skipped (BACKUP_S3_URI unset)"
fi

if is_dry; then
  echo "mode: dry-run"
  echo "backup_dir: $BACKUP_DIR"
  if [ -n "$RESTORE_DEST" ]; then
    echo "restore_dest: $(anchor "$RESTORE_DEST") (not written)"
  else
    echo "restore_dest: required when DRY_RUN=0 (not written)"
  fi
  if [ -n "$archive" ] && [ -f "$archive" ]; then
    echo "archive: $archive (not extracted)"
    if [ -n "${BACKUP_S3_URI:-}" ]; then
      echo "s3: local archive present; not downloaded"
    fi
    if [ -f "$sql" ]; then
      echo "postgres: $sql (not copied; psql is not run)"
    else
      echo "postgres: no matching sql (psql is not run)"
    fi
  elif [ -n "${BACKUP_S3_URI:-}" ] && [ -n "$BACKUP_ID" ]; then
    key="$(s3_object_key "delta_lake_${BACKUP_ID}.tar.gz")"
    echo "archive: s3://${S3_BUCKET}/${key} (not downloaded)"
    echo "s3: would download s3://${S3_BUCKET}/${key} to ${BACKUP_DIR}/delta_lake_${BACKUP_ID}.tar.gz (not downloaded)"
    echo "postgres: would copy a downloaded postgres_${BACKUP_ID}.sql when that object exists (not downloaded; psql is not run)"
  elif [ -n "${BACKUP_S3_URI:-}" ]; then
    echo "archive: none"
    echo "s3: BACKUP_ID is required to download when no local archive is present (not downloaded)"
    echo "restore: nothing to restore"
  else
    echo "archive: none"
    echo "restore: nothing to restore"
  fi
  if [ -n "${BACKUP_S3_URI:-}" ]; then
    s3_note_retention
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
  if [ -z "${BACKUP_S3_URI:-}" ]; then
    echo "archive not found: ${archive:-$BACKUP_DIR/delta_lake_*.tar.gz}" >&2
    exit 1
  fi
  if [ -z "$BACKUP_ID" ]; then
    echo "BACKUP_ID is required to restore from BACKUP_S3_URI when no local archive is present" >&2
    exit 2
  fi
  s3_install_cleanup_trap
  s3_prepare_real
  echo "s3_client: $S3_CLIENT"
  mkdir -p "$BACKUP_DIR"
  chosen_id="$BACKUP_ID"
  archive="$BACKUP_DIR/delta_lake_${chosen_id}.tar.gz"
  sql="$BACKUP_DIR/postgres_${chosen_id}.sql"
  key="$(s3_object_key "delta_lake_${chosen_id}.tar.gz")"
  if ! s3_has_object "$key"; then
    echo "s3 object not found: s3://${S3_BUCKET}/${key}" >&2
    exit 1
  fi
  s3_download_file "delta_lake_${chosen_id}.tar.gz" "$archive"
  key="$(s3_object_key "postgres_${chosen_id}.sql")"
  if s3_has_object "$key"; then
    s3_download_file "postgres_${chosen_id}.sql" "$sql"
  fi
elif [ -n "${BACKUP_S3_URI:-}" ]; then
  echo "s3: local archive present; not downloaded"
fi
if [ -n "${BACKUP_S3_URI:-}" ]; then
  s3_note_retention
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
