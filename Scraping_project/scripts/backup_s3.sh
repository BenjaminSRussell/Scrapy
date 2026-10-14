#!/usr/bin/env bash
# S3 / MinIO target for backup and restore (#706). Source this file; do not execute it.
# Callers run under set -euo pipefail. This file sets no shell options.
#
# BACKUP_S3_URI         s3://bucket or s3://bucket/prefix. Unset skips the network.
# BACKUP_S3_ENDPOINT    http(s) URL. Required for MinIO (compose publishes
#                       http://127.0.0.1:9000) and for mc. Omit for AWS.
# BACKUP_S3_CLIENT      aws, mc, or empty (aws if it is on PATH, else mc).
# AWS_ACCESS_KEY_ID     Required with AWS_SECRET_ACCESS_KEY when an endpoint is
# AWS_SECRET_ACCESS_KEY set or the client is mc. Never printed. aws reads them
#                       from the environment. mc receives them once, as `mc alias`
#                       arguments, inside a temp MC_CONFIG_DIR that is removed on exit.
# AWS_DEFAULT_REGION    Used for aws. Defaults to us-east-1 (MinIO accepts that
#                       without a location constraint). AWS_REGION wins when
#                       AWS_DEFAULT_REGION is unset.

s3_parse_uri() {
  local uri="$1" rest bucket prefix
  case "$uri" in
    s3://*) ;;
    *)
      echo "BACKUP_S3_URI must be s3://bucket or s3://bucket/prefix" >&2
      exit 2
      ;;
  esac
  rest="${uri#s3://}"
  rest="${rest%/}"
  case "$rest" in
    ""|*@*|*" "*|*"?"*|*"#"*|*"\\"*|//*)
      echo "BACKUP_S3_URI must be s3://bucket or s3://bucket/prefix with no credentials" >&2
      exit 2
      ;;
  esac
  bucket="${rest%%/*}"
  case "$bucket" in
    *[!A-Za-z0-9.-]*)
      echo "BACKUP_S3_URI bucket is invalid" >&2
      exit 2
      ;;
  esac
  if [ "${#bucket}" -lt 3 ] || [ "${#bucket}" -gt 63 ]; then
    echo "BACKUP_S3_URI bucket is invalid" >&2
    exit 2
  fi
  case "$bucket" in
    [A-Za-z0-9]*[A-Za-z0-9]) ;;
    *)
      echo "BACKUP_S3_URI bucket is invalid" >&2
      exit 2
      ;;
  esac
  case "$bucket" in
    .*|*.|*-)
      echo "BACKUP_S3_URI bucket is invalid" >&2
      exit 2
      ;;
  esac
  if [ "$rest" = "$bucket" ]; then
    prefix=""
  else
    prefix="${rest#*/}"
    case "$prefix" in
      ""|/*|*/|*..*|*"//"*)
        echo "BACKUP_S3_URI prefix is invalid" >&2
        exit 2
        ;;
    esac
    case "$prefix" in
      *[!A-Za-z0-9._/-]*)
        echo "BACKUP_S3_URI prefix is invalid" >&2
        exit 2
        ;;
    esac
  fi
  S3_BUCKET="$bucket"
  S3_PREFIX="$prefix"
}

s3_object_key() {
  local name="$1"
  if [ -n "${S3_PREFIX:-}" ]; then
    printf '%s/%s\n' "$S3_PREFIX" "$name"
  else
    printf '%s\n' "$name"
  fi
}

s3_validate_endpoint() {
  local ep="${BACKUP_S3_ENDPOINT:-}"
  [ -n "$ep" ] || return 0
  case "$ep" in
    http://*|https://*) ;;
    *)
      echo "BACKUP_S3_ENDPOINT must be an http or https URL" >&2
      exit 2
      ;;
  esac
  case "$ep" in
    *[[:space:]]*|*@*|*"?"*|*"#"*)
      echo "BACKUP_S3_ENDPOINT must not include credentials, spaces, or a query" >&2
      exit 2
      ;;
  esac
}

s3_resolve_client() {
  local want="${BACKUP_S3_CLIENT:-}"
  case "$want" in
    ""|aws|mc) ;;
    *)
      echo "BACKUP_S3_CLIENT must be aws or mc" >&2
      exit 2
      ;;
  esac
  if [ "$want" = "aws" ]; then
    command -v aws >/dev/null 2>&1 || { echo "BACKUP_S3_CLIENT=aws but aws is not on PATH" >&2; exit 1; }
    S3_CLIENT="aws"
  elif [ "$want" = "mc" ]; then
    command -v mc >/dev/null 2>&1 || { echo "BACKUP_S3_CLIENT=mc but mc is not on PATH" >&2; exit 1; }
    S3_CLIENT="mc"
  elif command -v aws >/dev/null 2>&1; then
    S3_CLIENT="aws"
  elif command -v mc >/dev/null 2>&1; then
    S3_CLIENT="mc"
  else
    echo "aws or mc is required to use BACKUP_S3_URI" >&2
    exit 1
  fi
  if [ "$S3_CLIENT" = "mc" ] && [ -z "${BACKUP_S3_ENDPOINT:-}" ]; then
    echo "BACKUP_S3_ENDPOINT is required when using mc" >&2
    exit 2
  fi
}

s3_require_credentials() {
  local id="${AWS_ACCESS_KEY_ID:-}" secret="${AWS_SECRET_ACCESS_KEY:-}"
  if [ -n "$id" ] && [ -z "$secret" ]; then
    echo "AWS_SECRET_ACCESS_KEY is required when AWS_ACCESS_KEY_ID is set" >&2
    exit 2
  fi
  if [ -z "$id" ] && [ -n "$secret" ]; then
    echo "AWS_ACCESS_KEY_ID is required when AWS_SECRET_ACCESS_KEY is set" >&2
    exit 2
  fi
  if [ -n "${BACKUP_S3_ENDPOINT:-}" ] || [ "${S3_CLIENT:-}" = "mc" ]; then
    if [ -z "$id" ] || [ -z "$secret" ]; then
      echo "AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are required for MinIO or mc" >&2
      exit 2
    fi
  fi
}

s3_print_target() {
  echo "s3_uri: ${BACKUP_S3_URI}"
  if [ -n "${BACKUP_S3_ENDPOINT:-}" ]; then
    echo "s3_endpoint: ${BACKUP_S3_ENDPOINT}"
  else
    echo "s3_endpoint: aws-default"
  fi
}

s3_note_retention() {
  echo "s3_retention: not applied by this script; set a bucket lifecycle expiration (remote objects are not deleted)"
}

s3_cleanup() {
  if [ -n "${S3_CFG:-}" ]; then
    rm -rf "$S3_CFG"
    S3_CFG=""
  fi
}

s3_install_cleanup_trap() {
  trap 's3_cleanup' EXIT
}

s3_session_start() {
  if [ -n "${S3_CFG:-}" ]; then
    return 0
  fi
  S3_CFG="$(mktemp -d "${TMPDIR:-/tmp}/scrapy-s3.XXXXXX")"
  if [ "$S3_CLIENT" = "mc" ]; then
    (
      export MC_CONFIG_DIR="$S3_CFG"
      mc alias set scrapybackup "$BACKUP_S3_ENDPOINT" "$AWS_ACCESS_KEY_ID" "$AWS_SECRET_ACCESS_KEY" >/dev/null
    )
  elif [ -n "${BACKUP_S3_ENDPOINT:-}" ]; then
    cat > "$S3_CFG/config" <<'EOF'
[default]
s3 =
    addressing_style = path
EOF
  fi
}

s3_prepare_dry() {
  s3_parse_uri "$BACKUP_S3_URI"
  s3_validate_endpoint
}

s3_prepare_real() {
  s3_parse_uri "$BACKUP_S3_URI"
  s3_validate_endpoint
  s3_resolve_client
  s3_require_credentials
  s3_session_start
}

aws_s3() {
  (
    if [ -n "${AWS_DEFAULT_REGION:-}" ]; then
      export AWS_DEFAULT_REGION
    elif [ -n "${AWS_REGION:-}" ]; then
      export AWS_DEFAULT_REGION="$AWS_REGION"
    else
      export AWS_DEFAULT_REGION="us-east-1"
    fi
    if [ -n "${BACKUP_S3_ENDPOINT:-}" ]; then
      export AWS_CONFIG_FILE="${S3_CFG}/config"
      # MinIO rejects the default checksum headers on some AWS CLI builds.
      export AWS_REQUEST_CHECKSUM_CALCULATION=when_required
      export AWS_RESPONSE_CHECKSUM_VALIDATION=when_required
      aws --endpoint-url "$BACKUP_S3_ENDPOINT" "$@"
    else
      aws "$@"
    fi
  )
}

mc_do() {
  (
    export MC_CONFIG_DIR="$S3_CFG"
    mc "$@"
  )
}

s3_ensure_bucket() {
  if [ "${S3_BUCKET_READY:-0}" = "1" ]; then
    return 0
  fi
  if [ "$S3_CLIENT" = "aws" ]; then
    if ! aws_s3 s3api head-bucket --bucket "$S3_BUCKET" >/dev/null 2>&1; then
      aws_s3 s3 mb "s3://${S3_BUCKET}" >/dev/null
    fi
  else
    if ! mc_do stat "scrapybackup/${S3_BUCKET}" >/dev/null 2>&1; then
      mc_do mb -p "scrapybackup/${S3_BUCKET}" >/dev/null
    fi
  fi
  S3_BUCKET_READY=1
}

s3_upload_file() {
  local src="$1" name="$2" key
  key="$(s3_object_key "$name")"
  s3_ensure_bucket
  if [ "$S3_CLIENT" = "aws" ]; then
    aws_s3 s3 cp "$src" "s3://${S3_BUCKET}/${key}" --only-show-errors
  else
    mc_do cp "$src" "scrapybackup/${S3_BUCKET}/${key}" >/dev/null
  fi
  echo "uploaded: s3://${S3_BUCKET}/${key}"
}

s3_has_object() {
  local key="$1"
  if [ "$S3_CLIENT" = "aws" ]; then
    aws_s3 s3api head-object --bucket "$S3_BUCKET" --key "$key" >/dev/null 2>&1
  else
    mc_do stat "scrapybackup/${S3_BUCKET}/${key}" >/dev/null 2>&1
  fi
}

s3_download_file() {
  local name="$1" dest="$2" key
  key="$(s3_object_key "$name")"
  if [ "$S3_CLIENT" = "aws" ]; then
    aws_s3 s3 cp "s3://${S3_BUCKET}/${key}" "$dest" --only-show-errors
  else
    mc_do cp "scrapybackup/${S3_BUCKET}/${key}" "$dest" >/dev/null
  fi
  echo "downloaded: s3://${S3_BUCKET}/${key} -> $dest"
}
