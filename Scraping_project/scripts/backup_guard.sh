#!/usr/bin/env bash
# Path rules for backup and restore (#300). Source this file; do not execute it.
# ROOT is the Scraping_project directory. Real runs may only use /tmp or this
# worktree, and never data/delta_lake. reject_lake does not stat the lake.

reject_lake() {
  local label="$1" raw="$2" norm root_real lake
  [ -n "$raw" ] || return 0
  raw="${raw%/}"
  case "$raw" in
    /*) ;;
    *) raw="$ROOT/$raw" ;;
  esac
  norm="$(python3 -c 'import os,sys; print(os.path.normpath(sys.argv[1]))' "$raw")" || exit 2
  root_real="$(cd "$ROOT" && pwd -P)"
  lake="$root_real/data/delta_lake"
  case "$norm" in
    "$ROOT/data/delta_lake"|"$ROOT/data/delta_lake"/*|"$lake"|"$lake"/*)
      echo "refusing $label on data/delta_lake" >&2
      exit 2
      ;;
  esac
}

guard_path() {
  local label="$1" raw="$2" real tmp repo root_real lake
  [ -n "$raw" ] || { echo "$label is required when DRY_RUN=0" >&2; exit 2; }
  raw="${raw%/}"
  case "$raw" in
    /*) ;;
    *) raw="$ROOT/$raw" ;;
  esac
  reject_lake "$label" "$raw"
  real="$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$raw")" || exit 2
  tmp="$(cd /tmp && pwd -P)"
  repo="$(cd "$ROOT/.." && pwd -P)"
  root_real="$(cd "$ROOT" && pwd -P)"
  lake="$root_real/data/delta_lake"
  case "$real" in
    "$lake"|"$lake"/*)
      echo "refusing $label on data/delta_lake" >&2
      exit 2
      ;;
  esac
  case "$real" in
    "$tmp"/*|"$repo"/*) ;;
    *)
      echo "refusing $label outside /tmp or the worktree: $real" >&2
      exit 2
      ;;
  esac
  case "$real" in
    "$root_real")
      echo "refusing $label at the project root" >&2
      exit 2
      ;;
  esac
}
