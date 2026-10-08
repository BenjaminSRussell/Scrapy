#!/bin/bash
# ==================================================================
# Access Grafana - Quick Port Forward Script
# ==================================================================

set -euo pipefail  # project standard (#822): scripts/SHELL_STANDARD.md

echo "=========================================="
echo "  Grafana Port Forward"
echo "=========================================="
echo ""

# Check if Grafana pod is running
# Capture first: under pipefail `kubectl ... | grep -q` can fail when grep exits
# early and kubectl gets SIGPIPE, which would misreport a running pod.
GRAFANA_PODS="$(kubectl get pods -l app=grafana 2>/dev/null || true)"
if ! grep -q "Running" <<<"$GRAFANA_PODS"; then
    echo "❌ Grafana pod is not running!"
    echo ""
    echo "Deploy Grafana first with:"
    echo "  kubectl apply -f k8s/grafana-standalone.yaml"
    exit 1
fi

echo "✅ Grafana pod is running"
echo ""
echo "Starting port-forward to localhost:3000..."
echo ""
echo "=========================================="
echo "  Access Information"
echo "=========================================="
echo ""
echo "  URL:      http://localhost:3000"
echo "  Username: admin"
echo "  Password: admin"
echo ""
echo "=========================================="
echo ""
echo "Press Ctrl+C to stop port forwarding"
echo ""

# Start port-forward
kubectl port-forward svc/grafana 3000:3000
