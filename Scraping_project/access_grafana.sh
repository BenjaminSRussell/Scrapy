#!/bin/bash
# ==================================================================
# Access Grafana - Quick Port Forward Script
# ==================================================================

set -e

echo "=========================================="
echo "  Grafana Port Forward"
echo "=========================================="
echo ""

# Check if Grafana pod is running
if ! kubectl get pods -l app=grafana | grep -q "Running"; then
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
