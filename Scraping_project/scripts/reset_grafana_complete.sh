#!/bin/bash
# ==================================================================
# Complete Grafana Reset Script - Docker and Kubernetes
# ==================================================================
# This script completely resets Grafana by:
# 1. Stopping and removing containers/pods
# 2. Removing volumes and persistent data
# 3. Recreating with admin/admin credentials
# ==================================================================

set -e

echo "=========================================="
echo "  Grafana Complete Reset Script"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored messages
print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check environment
if [ -f "docker-compose.yml" ]; then
    ENV_TYPE="docker"
    print_info "Docker Compose environment detected"
elif command -v kubectl &> /dev/null && kubectl cluster-info &> /dev/null; then
    ENV_TYPE="kubernetes"
    print_info "Kubernetes environment detected"
else
    print_error "Could not detect environment (Docker or Kubernetes)"
    exit 1
fi

echo ""
echo "=========================================="
echo "  Step 1: Stopping Grafana"
echo "=========================================="

if [ "$ENV_TYPE" = "docker" ]; then
    print_info "Stopping Grafana container..."
    docker-compose stop grafana || print_warning "Grafana container not running"

    print_info "Removing Grafana container..."
    docker-compose rm -f grafana || print_warning "Grafana container not found"

elif [ "$ENV_TYPE" = "kubernetes" ]; then
    print_info "Deleting Grafana pods..."
    kubectl delete pods -l app.kubernetes.io/component=grafana --force --grace-period=0 || print_warning "No Grafana pods found"

    print_info "Scaling down Grafana deployment..."
    kubectl scale deployment --replicas=0 -l app.kubernetes.io/component=grafana || print_warning "No Grafana deployment found"
fi

echo ""
echo "=========================================="
echo "  Step 2: Removing Persistent Data"
echo "=========================================="

if [ "$ENV_TYPE" = "docker" ]; then
    print_info "Removing Grafana volume..."
    docker volume rm scraping_project_grafana_data 2>/dev/null || \
    docker volume rm scraping-project_grafana_data 2>/dev/null || \
    docker volume rm scraping_grafana_data 2>/dev/null || \
    print_warning "Grafana volume not found (may already be deleted)"

elif [ "$ENV_TYPE" = "kubernetes" ]; then
    print_info "Deleting Grafana PVC..."
    kubectl delete pvc -l app.kubernetes.io/component=grafana || print_warning "No Grafana PVC found"

    print_info "Waiting for PVC deletion..."
    sleep 5
fi

echo ""
echo "=========================================="
echo "  Step 3: Resetting Credentials"
echo "=========================================="

if [ "$ENV_TYPE" = "docker" ]; then
    # Update .env file
    if [ -f ".env" ]; then
        print_info "Updating .env file with admin/admin credentials..."
        if grep -q "GRAFANA_ADMIN_PASSWORD=" .env; then
            sed -i.bak 's/^GRAFANA_ADMIN_PASSWORD=.*/GRAFANA_ADMIN_PASSWORD=admin/' .env
            print_info "Updated GRAFANA_ADMIN_PASSWORD to 'admin'"
        else
            echo "GRAFANA_ADMIN_PASSWORD=admin" >> .env
            print_info "Added GRAFANA_ADMIN_PASSWORD=admin to .env"
        fi
    else
        print_warning ".env file not found, creating one..."
        cat > .env << 'EOF'
# Database connection settings
DB_HOST=localhost
DB_PORT=5432
DB_NAME=scraping_pipeline
DB_USER=postgres
DB_PASSWORD=postgres

# Grafana admin credentials
GRAFANA_ADMIN_PASSWORD=admin
EOF
        print_info "Created .env file with default credentials"
    fi

elif [ "$ENV_TYPE" = "kubernetes" ]; then
    print_info "Deleting existing Grafana secret..."
    kubectl delete secret grafana-credentials 2>/dev/null || print_warning "Secret not found"

    print_info "Creating new Grafana secret with admin/admin..."
    kubectl create secret generic grafana-credentials \
        --from-literal=admin-user=admin \
        --from-literal=admin-password=admin

    print_info "Secret created successfully"
fi

echo ""
echo "=========================================="
echo "  Step 4: Restarting Grafana"
echo "=========================================="

if [ "$ENV_TYPE" = "docker" ]; then
    print_info "Starting Grafana with fresh configuration..."
    docker-compose up -d grafana

    print_info "Waiting for Grafana to be healthy..."
    sleep 10

    # Wait for health check
    for i in {1..30}; do
        if docker-compose ps | grep grafana | grep -q "healthy"; then
            print_info "Grafana is healthy!"
            break
        fi
        echo -n "."
        sleep 2
    done
    echo ""

elif [ "$ENV_TYPE" = "kubernetes" ]; then
    print_info "Scaling up Grafana deployment..."
    kubectl scale deployment --replicas=1 -l app.kubernetes.io/component=grafana

    print_info "Waiting for Grafana pod to be ready..."
    kubectl wait --for=condition=ready pod -l app.kubernetes.io/component=grafana --timeout=120s

    print_info "Grafana pod is ready!"
fi

echo ""
echo "=========================================="
echo "  Step 5: Verifying Access"
echo "=========================================="

if [ "$ENV_TYPE" = "docker" ]; then
    GRAFANA_URL="http://localhost:3000"
    print_info "Testing Grafana access at ${GRAFANA_URL}..."

    sleep 5
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${GRAFANA_URL}/api/health" || echo "000")

    if [ "$HTTP_CODE" = "200" ]; then
        print_info "Grafana is accessible!"
    else
        print_warning "Grafana may not be fully ready yet (HTTP $HTTP_CODE)"
    fi

elif [ "$ENV_TYPE" = "kubernetes" ]; then
    print_info "Getting Grafana service information..."
    kubectl get svc -l app.kubernetes.io/component=grafana

    print_info "Setting up port forward to access Grafana..."
    print_warning "Run this command in another terminal to access Grafana:"
    echo "    kubectl port-forward svc/\$(kubectl get svc -l app.kubernetes.io/component=grafana -o name | head -1 | cut -d/ -f2) 3000:3000"
fi

echo ""
echo "=========================================="
echo "  Reset Complete!"
echo "=========================================="
echo ""
print_info "Grafana Credentials:"
echo "  Username: admin"
echo "  Password: admin"
echo ""

if [ "$ENV_TYPE" = "docker" ]; then
    print_info "Access Grafana at: http://localhost:3000"
    print_info "View logs with: docker-compose logs -f grafana"
elif [ "$ENV_TYPE" = "kubernetes" ]; then
    print_info "Access Grafana using port-forward"
    print_info "View logs with: kubectl logs -l app.kubernetes.io/component=grafana -f"
fi

echo ""
print_warning "Note: You may need to reconfigure dashboards and datasources"
print_warning "      if they are not automatically provisioned."
echo ""
