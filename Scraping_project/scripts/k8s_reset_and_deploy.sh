#!/bin/bash
# ==================================================================
# Kubernetes Reset and Redeploy Script
# ==================================================================
# This script removes the old "coco" deployment and redeploys
# with standardized naming: scraping-pipeline-*
# ==================================================================

set -e

echo "=========================================="
echo "  Kubernetes Reset and Redeploy"
echo "=========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# Configuration
OLD_RELEASE_NAME="coco"
NEW_RELEASE_NAME="scraping-pipeline"
CHART_PATH="k8s/helm/scraping-pipeline"
NAMESPACE="default"

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    print_error "kubectl not found. Please install kubectl first."
    exit 1
fi

# Check if helm is available
if ! command -v helm &> /dev/null; then
    print_error "helm not found. Please install helm first."
    exit 1
fi

# Verify cluster connection
print_info "Checking Kubernetes cluster connection..."
if ! kubectl cluster-info &> /dev/null; then
    print_error "Cannot connect to Kubernetes cluster. Please check your kubeconfig."
    exit 1
fi
print_info "Connected to Kubernetes cluster"

echo ""
print_warning "This will:"
print_warning "  1. Delete the '${OLD_RELEASE_NAME}' Helm release"
print_warning "  2. Remove all associated resources (PVCs, ConfigMaps, Secrets)"
print_warning "  3. Redeploy with name '${NEW_RELEASE_NAME}'"
echo ""
read -p "Continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    print_info "Aborted by user"
    exit 0
fi

echo ""
print_step "Step 1: Listing current releases..."
echo "=========================================="
print_info "Current Helm releases:"
helm list -n "$NAMESPACE"

echo ""
print_step "Step 2: Uninstalling old release '${OLD_RELEASE_NAME}'..."
echo "=========================================="

if helm list -n "$NAMESPACE" | grep -q "^${OLD_RELEASE_NAME}"; then
    print_info "Uninstalling Helm release '${OLD_RELEASE_NAME}'..."
    helm uninstall "$OLD_RELEASE_NAME" -n "$NAMESPACE" || print_warning "Failed to uninstall cleanly"
    print_info "Waiting for resources to be cleaned up..."
    sleep 10
else
    print_warning "Release '${OLD_RELEASE_NAME}' not found"
fi

echo ""
print_step "Step 3: Cleaning up remaining resources..."
echo "=========================================="

# Clean up PVCs with coco prefix
print_info "Removing PVCs with '${OLD_RELEASE_NAME}' prefix..."
kubectl delete pvc -l "app.kubernetes.io/instance=${OLD_RELEASE_NAME}" -n "$NAMESPACE" --wait=true 2>/dev/null || print_warning "No PVCs found"

# Clean up ConfigMaps
print_info "Removing ConfigMaps with '${OLD_RELEASE_NAME}' prefix..."
kubectl delete configmap -l "app.kubernetes.io/instance=${OLD_RELEASE_NAME}" -n "$NAMESPACE" 2>/dev/null || print_warning "No ConfigMaps found"

# Clean up Secrets
print_info "Removing Secrets with '${OLD_RELEASE_NAME}' prefix..."
kubectl delete secret -l "app.kubernetes.io/instance=${OLD_RELEASE_NAME}" -n "$NAMESPACE" 2>/dev/null || print_warning "No Secrets found"

# Manual cleanup of any remaining resources
print_info "Checking for remaining '${OLD_RELEASE_NAME}' resources..."
kubectl get all -n "$NAMESPACE" | grep "${OLD_RELEASE_NAME}" || print_info "No remaining resources found"

echo ""
print_step "Step 4: Preparing for new deployment..."
echo "=========================================="

# Ensure secrets exist
print_info "Creating fresh secrets..."

# Delete old secrets if they exist
kubectl delete secret postgres-credentials -n "$NAMESPACE" 2>/dev/null || true
kubectl delete secret grafana-credentials -n "$NAMESPACE" 2>/dev/null || true

# Create postgres secret
print_info "Creating PostgreSQL credentials secret..."
kubectl create secret generic postgres-credentials \
    --from-literal=password=postgres \
    --from-literal=DB_PASSWORD=postgres \
    -n "$NAMESPACE"

# Create Grafana secret with admin/admin
print_info "Creating Grafana credentials secret (admin/admin)..."
kubectl create secret generic grafana-credentials \
    --from-literal=admin-user=admin \
    --from-literal=admin-password=admin \
    -n "$NAMESPACE"

print_info "Secrets created successfully"

echo ""
print_step "Step 5: Validating Helm chart..."
echo "=========================================="

if [ ! -d "$CHART_PATH" ]; then
    print_error "Chart path not found: $CHART_PATH"
    exit 1
fi

print_info "Linting Helm chart..."
helm lint "$CHART_PATH" || print_warning "Chart has linting warnings"

print_info "Validating chart syntax..."
helm template "$NEW_RELEASE_NAME" "$CHART_PATH" > /dev/null || {
    print_error "Chart template validation failed"
    exit 1
}

print_info "Chart validation successful"

echo ""
print_step "Step 6: Deploying new release '${NEW_RELEASE_NAME}'..."
echo "=========================================="

print_info "Installing Helm chart with standardized naming..."
helm install "$NEW_RELEASE_NAME" "$CHART_PATH" \
    --namespace "$NAMESPACE" \
    --create-namespace \
    --set secrets.grafana.adminUser=admin \
    --set secrets.grafana.adminPassword=admin \
    --set secrets.postgres.password=postgres \
    --wait \
    --timeout 10m

print_info "Deployment initiated successfully"

echo ""
print_step "Step 7: Waiting for pods to be ready..."
echo "=========================================="

print_info "Waiting for core infrastructure..."
sleep 15

# Wait for key services
KEY_SERVICES=("redis" "postgresql" "kafka" "prometheus" "grafana")

for service in "${KEY_SERVICES[@]}"; do
    print_info "Waiting for ${service} pods..."
    kubectl wait --for=condition=ready pod \
        -l "app.kubernetes.io/component=${service}" \
        -n "$NAMESPACE" \
        --timeout=300s 2>/dev/null || print_warning "${service} pods may not be ready yet"
done

echo ""
print_step "Step 8: Verifying deployment..."
echo "=========================================="

print_info "Deployed resources:"
echo ""

echo "=== Pods ==="
kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${NEW_RELEASE_NAME}"

echo ""
echo "=== Services ==="
kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${NEW_RELEASE_NAME}"

echo ""
echo "=== PVCs ==="
kubectl get pvc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${NEW_RELEASE_NAME}"

echo ""
echo "=== ConfigMaps ==="
kubectl get configmap -n "$NAMESPACE" -l "app.kubernetes.io/instance=${NEW_RELEASE_NAME}"

echo ""
echo "=== Secrets ==="
kubectl get secret -n "$NAMESPACE" -l "app.kubernetes.io/instance=${NEW_RELEASE_NAME}"

echo ""
print_step "Step 9: Service naming verification..."
echo "=========================================="

print_info "New service naming convention (standardized):"
kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${NEW_RELEASE_NAME}" -o custom-columns=NAME:.metadata.name,TYPE:.spec.type,PORTS:.spec.ports[*].port

echo ""
print_step "Step 10: Access information..."
echo "=========================================="

# Get Grafana service name
GRAFANA_SVC=$(kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/component=grafana" -o jsonpath='{.items[0].metadata.name}')

if [ -n "$GRAFANA_SVC" ]; then
    print_info "Grafana Service: ${GRAFANA_SVC}"
    print_info "To access Grafana, run:"
    echo "    kubectl port-forward svc/${GRAFANA_SVC} 3000:3000 -n ${NAMESPACE}"
    echo ""
    print_info "Then access at: http://localhost:3000"
    print_info "Login credentials: admin / admin"
fi

# Get Prometheus service
PROM_SVC=$(kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/component=prometheus" -o jsonpath='{.items[0].metadata.name}')

if [ -n "$PROM_SVC" ]; then
    echo ""
    print_info "Prometheus Service: ${PROM_SVC}"
    print_info "To access Prometheus, run:"
    echo "    kubectl port-forward svc/${PROM_SVC} 9090:9100 -n ${NAMESPACE}"
fi

echo ""
echo "=========================================="
echo "  Deployment Complete!"
echo "=========================================="
echo ""
print_info "Service Naming:"
echo "  OLD: coco-scraping-pipeline-* (removed)"
echo "  NEW: scraping-pipeline-* (deployed)"
echo ""
print_info "Useful Commands:"
echo "  • View all pods:        kubectl get pods -n ${NAMESPACE}"
echo "  • View logs:            kubectl logs -f <pod-name> -n ${NAMESPACE}"
echo "  • View services:        kubectl get svc -n ${NAMESPACE}"
echo "  • Helm status:          helm status ${NEW_RELEASE_NAME} -n ${NAMESPACE}"
echo "  • Delete deployment:    helm uninstall ${NEW_RELEASE_NAME} -n ${NAMESPACE}"
echo ""
print_warning "Note: If you have ingress configured, update your DNS/hosts file:"
echo "  grafana.local -> <ingress-controller-ip>"
echo ""
