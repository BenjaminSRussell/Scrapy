#!/bin/bash
# ==================================================================
# Access Grafana: Docker Compose (local) or Kubernetes (#382)
#
#   ./access_grafana.sh            auto: a running Kubernetes Grafana pod is
#                                  port-forwarded; otherwise the local Compose
#                                  Grafana is used if it is running
#   ./access_grafana.sh --local    Docker Compose only (http://localhost:3000)
#   ./access_grafana.sh --k8s      Kubernetes only (kubectl port-forward)
#
# Local Compose needs no port-forward: docker-compose.yml publishes
# Grafana on http://localhost:3000. Start it with `python start.py` or
# `docker compose up -d grafana`.
# ==================================================================

set -euo pipefail  # project standard (#822): scripts/SHELL_STANDARD.md

MODE="${1:-auto}"
case "$MODE" in
    auto|--auto) MODE=auto ;;
    --local|local) MODE=local ;;
    --k8s|k8s) MODE=k8s ;;
    -h|--help)
        sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
        exit 0 ;;
    *) echo "Unknown option: $MODE (use --local, --k8s or no argument)" >&2; exit 2 ;;
esac

LOCAL_URL="http://localhost:3000"

print_local() {
    echo "=========================================="
    echo "  Grafana (Docker Compose)"
    echo "=========================================="
    echo ""
    echo "  URL:      ${LOCAL_URL}"
    echo "  Username: admin"
    echo "  Password: GRAFANA_ADMIN_PASSWORD from .env (default 'admin', local dev only)"
    echo "  Dashboard: ${LOCAL_URL}/d/scraping-pipeline-health"
    echo ""
}

# Compose Grafana running? Uses scripts/compose_lib.sh (docker compose v2 or docker-compose).
local_grafana_running() {
    command -v docker >/dev/null 2>&1 || command -v docker-compose >/dev/null 2>&1 || return 1
    # shellcheck source=scripts/compose_lib.sh
    . "$(dirname "${BASH_SOURCE[0]}")/scripts/compose_lib.sh"
    local running
    local dir
    dir="$(dirname "${BASH_SOURCE[0]}")"
    # v2: --status running; v1 docker-compose: --filter status=running
    running="$(cd "$dir" && { compose ps --services --status running 2>/dev/null \
        || compose ps --services --filter status=running 2>/dev/null || true; })"
    grep -qx "grafana" <<<"$running"
}

k8s_grafana_running() {
    command -v kubectl >/dev/null 2>&1 || return 1
    # Capture first: under pipefail `kubectl ... | grep -q` can fail when grep exits
    # early and kubectl gets SIGPIPE, which would misreport a running pod.
    local pods
    pods="$(kubectl get pods -l app=grafana 2>/dev/null || true)"
    grep -q "Running" <<<"$pods"
}

port_forward() {
    echo "=========================================="
    echo "  Grafana (Kubernetes) port-forward"
    echo "=========================================="
    echo ""
    echo "✅ Grafana pod is running"
    echo ""
    echo "  URL:      ${LOCAL_URL}"
    echo "  Username: admin"
    echo "  Password: the Grafana admin Secret (Helm: secrets.grafana.adminPassword)"
    echo ""
    echo "Press Ctrl+C to stop port forwarding"
    echo ""
    kubectl port-forward svc/grafana 3000:3000
}

if [ "$MODE" = "local" ]; then
    print_local
    if ! local_grafana_running; then
        echo "⚠️  The Compose Grafana is not running. Start it with: docker compose up -d grafana"
        exit 1
    fi
    exit 0
fi

if [ "$MODE" = "k8s" ] || k8s_grafana_running; then
    if ! k8s_grafana_running; then
        echo "❌ Grafana pod is not running!"
        echo ""
        echo "Deploy Grafana first with:"
        echo "  kubectl apply -f k8s/grafana-standalone.yaml"
        exit 1
    fi
    port_forward
    exit 0
fi

if local_grafana_running; then
    print_local
    exit 0
fi

echo "❌ Grafana is not running (no Kubernetes pod, no Compose container)."
echo ""
echo "  Local:      python start.py   (or: docker compose up -d grafana), then open ${LOCAL_URL}"
echo "  Kubernetes: kubectl apply -f k8s/grafana-standalone.yaml, then ./access_grafana.sh --k8s"
exit 1
