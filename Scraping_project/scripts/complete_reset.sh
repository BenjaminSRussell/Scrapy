#!/usr/bin/env bash
# ==================================================================
# Complete Stack Reset and Rebuild Script
# ==================================================================
# 1. Stops all services and removes this Compose project's volumes
#    (`docker compose down -v`, so the volume list always matches the file)
# 2. Optionally removes and rebuilds images
# 3. Restarts the stack in dependency order
#
# Only services defined in the active Compose file are started (#326):
# services that belong to the full Kafka/exporter stack (see #145) are
# reported as skipped instead of failing `docker compose up`.
#
# Usage: scripts/complete_reset.sh [--yes] [--rebuild|--no-rebuild] [--dry-run]
#                                   [--execute --i-really-mean-it [--break-glass]]
#   --dry-run   print the plan (services per step) and exit without touching Docker
#
# Guarded (#522, #576): removing the volumes (incl. delta_data = the lake) needs
# --execute --i-really-mean-it AND ALLOW_LAKE_RESET=1; ENV=production also needs
# --break-glass plus a typed confirmation. Without them this is a dry run.
# Every attempt is audited to data/logs/destructive_ops.jsonl.
# ==================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
. "$SCRIPT_DIR/compose_lib.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
print_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

ASSUME_YES=0; DRY_RUN=0; REBUILD_IMAGES=""; GUARD_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --yes|-y) ASSUME_YES=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --rebuild) REBUILD_IMAGES=yes ;;
        --no-rebuild) REBUILD_IMAGES=no ;;
        --execute|--i-really-mean-it|--break-glass) GUARD_ARGS+=("$arg") ;;
        -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
        *) print_error "unknown argument: $arg"; exit 2 ;;
    esac
done

# Startup order. Names outside the current Compose file are filtered out.
INFRA=(redis postgres zookeeper kafka)
MONITORING=(prometheus prometheus-a prometheus-b alertmanager alertmanager-1 alertmanager-2 alertmanager-3 grafana)
EXPORTERS=(redis-exporter postgres-exporter kafka-jmx-exporter statsd-exporter metrics-exporter)
APPS=(scraper scrapy-app stage1-worker stage2-worker stage3-worker stage4-worker kafka-delta-ingestor)

if ! DEFINED="$(compose_services)" || [ -z "$DEFINED" ]; then
    print_error "Could not list Compose services. Run from a checkout with docker-compose.yml and Docker installed."
    exit 1
fi

infra_up="$(compose_filter "${INFRA[@]}")"
monitoring_up="$(compose_filter "${MONITORING[@]}")"
exporters_up="$(compose_filter "${EXPORTERS[@]}")"
apps_up="$(compose_filter "${APPS[@]}")"

if [ "$DRY_RUN" = "1" ]; then
    echo "infra: ${infra_up}"
    echo "monitoring: ${monitoring_up}"
    echo "exporters: ${exporters_up}"
    echo "apps: ${apps_up}"
    exit 0
fi

# Guard: dry run / dual confirmation / production break-glass / audit (#522, #576)
PY="${PYTHON:-python3}"
[ -x .venv/bin/python ] && PY="${PYTHON:-.venv/bin/python}"
GUARD_RC=0
"$PY" -m src.utils.destructive_guard complete-reset "compose-project-volumes:$(pwd)" \
    ${GUARD_ARGS[@]+"${GUARD_ARGS[@]}"} || GUARD_RC=$?
if [ "$GUARD_RC" -eq 10 ]; then
    print_info "Dry run: would run 'docker compose down -v' (every volume of this project, incl. delta_data = the lake) and rebuild. Nothing changed."
    exit 0
elif [ "$GUARD_RC" -ne 0 ]; then
    print_error "Refused (see above)."
    exit 3
fi

echo "=========================================="
echo "  Complete Stack Reset and Rebuild"
echo "=========================================="
print_warning "This will DELETE ALL DATA in this Compose project's volumes and rebuild the stack!"
if [ "$ASSUME_YES" != "1" ]; then
    read -r -p "Are you sure you want to continue? (yes/no): " CONFIRM
    if [ "$CONFIRM" != "yes" ]; then
        print_info "Aborted by user"
        exit 0
    fi
fi

print_step "Step 1: Stopping all services and removing project volumes..."
compose down -v --remove-orphans || print_warning "Some services may not be running"

print_step "Step 2: Removing old images (optional)..."
if [ -z "$REBUILD_IMAGES" ]; then
    if [ "$ASSUME_YES" = "1" ]; then
        REBUILD_IMAGES=no
    else
        read -r -p "Remove and rebuild Docker images? (yes/no): " REBUILD_IMAGES
    fi
fi
if [ "$REBUILD_IMAGES" = "yes" ]; then
    compose down --rmi local || print_warning "No images to remove"
fi

print_step "Step 3: Verifying .env configuration..."
if [ ! -f ".env" ]; then
    print_warning ".env file not found, creating from .env.example"
    cp .env.example .env
fi
if ! grep -q "^GRAFANA_ADMIN_PASSWORD=." .env; then
    print_warning "GRAFANA_ADMIN_PASSWORD not set in .env; Grafana falls back to the local-dev default"
fi
if ! grep -q "^DB_PASSWORD=." .env; then
    print_warning "DB_PASSWORD not set in .env, using default 'postgres'"
    if grep -q "^DB_PASSWORD=" .env; then
        sed -i.bak 's/^DB_PASSWORD=.*/DB_PASSWORD=postgres/' .env
    else
        echo "DB_PASSWORD=postgres" >> .env
    fi
fi
# Never echo .env values (secrets); list the keys only.
print_info "Environment keys: $(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env | tr -d '=' | tr '\n' ' ')"

print_step "Step 4: Building Docker images..."
if [ "$REBUILD_IMAGES" = "yes" ]; then
    compose build --no-cache
else
    compose build
fi

wait_running() {
    local service=$1 i
    for i in $(seq 1 30); do
        if compose ps "$service" 2>/dev/null | grep -qE "healthy|Up|running"; then
            print_info "${service} is ready"
            return 0
        fi
        sleep 2
    done
    print_warning "${service} not ready after 60s (docker compose logs ${service})"
}

start_group() {
    local label=$1; shift
    if [ "$#" -eq 0 ]; then
        print_info "No ${label} services defined in this Compose file"
        return 0
    fi
    print_info "Starting ${label}: $*"
    compose up -d "$@"
}

print_step "Step 5: Starting infrastructure..."
# shellcheck disable=SC2086  # word-splitting of the filtered lists is intended
start_group infrastructure $infra_up
for s in $infra_up; do wait_running "$s"; done

print_step "Step 6: Starting monitoring..."
# shellcheck disable=SC2086
start_group monitoring $monitoring_up

print_step "Step 7: Starting exporters..."
# shellcheck disable=SC2086
start_group exporter $exporters_up

print_step "Step 8: Starting application services..."
# shellcheck disable=SC2086
start_group application $apps_up

print_step "Step 9: Verifying stack status..."
compose ps

check_endpoint() {
    local url=$1 name=$2 code
    code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
    if [ "$code" = "200" ] || [ "$code" = "302" ]; then
        print_info "✓ ${name} is accessible (HTTP ${code})"
    else
        print_warning "✗ ${name} may not be ready (HTTP ${code})"
    fi
}
if compose_has grafana; then check_endpoint "http://localhost:3000" "Grafana"; fi
if compose_has prometheus; then check_endpoint "http://localhost:9090/-/ready" "Prometheus"; fi

echo ""
echo "=========================================="
echo "  Reset Complete!"
echo "=========================================="
echo "  • View all logs:      docker compose logs -f"
echo "  • Check status:       docker compose ps"
echo "  • Stop all:           docker compose down"
echo "  • Restart service:    docker compose restart <service>   (services: $(echo $DEFINED))"
