#!/bin/bash
# ==================================================================
# REBUILD ENVIRONMENT - One Command to Reset Everything
# ==================================================================
# This is the master reset script that handles both Docker and K8s
# Usage: ./rebuild_env.sh [docker|k8s|both]
# ==================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m'

print_banner() {
    echo -e "${CYAN}"
    echo "╔════════════════════════════════════════════════════════════╗"
    echo "║                                                            ║"
    echo "║         SCRAPING PIPELINE - ENVIRONMENT REBUILD           ║"
    echo "║                                                            ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_info() { echo -e "${GREEN}[✓]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[!]${NC} $1"; }
print_error() { echo -e "${RED}[✗]${NC} $1"; }
print_step() { echo -e "${BLUE}[→]${NC} $1"; }
print_section() { echo -e "\n${MAGENTA}━━━ $1 ━━━${NC}\n"; }

# Check if running from correct directory
if [ ! -f "docker-compose.yml" ]; then
    print_error "Must run from project root directory (where docker-compose.yml is located)"
    exit 1
fi

print_banner

# Detect what to rebuild
MODE=${1:-ask}

if [ "$MODE" = "ask" ]; then
    echo ""
    echo "What would you like to rebuild?"
    echo ""
    echo "  1) Docker Compose only"
    echo "  2) Kubernetes only"
    echo "  3) Both Docker and Kubernetes"
    echo "  4) Exit"
    echo ""
    read -p "Choose [1-4]: " choice

    case $choice in
        1) MODE="docker" ;;
        2) MODE="k8s" ;;
        3) MODE="both" ;;
        4) exit 0 ;;
        *) print_error "Invalid choice"; exit 1 ;;
    esac
fi

echo ""
print_section "Configuration"

print_info "Rebuild mode: ${MODE}"
print_info "Working directory: $(pwd)"

# Confirm
echo ""
print_warning "This will DELETE ALL DATA and rebuild from scratch!"
print_warning "Including: volumes, containers, pods, PVCs, and all stored data"
echo ""
read -p "Are you absolutely sure? Type 'yes' to continue: " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    print_info "Aborted by user"
    exit 0
fi

# ==========================================
# DOCKER COMPOSE REBUILD
# ==========================================
if [ "$MODE" = "docker" ] || [ "$MODE" = "both" ]; then
    print_section "Docker Compose Rebuild"

    print_step "Step 1: Stopping all services..."
    docker-compose down -v 2>/dev/null || true
    print_info "Services stopped"

    print_step "Step 2: Removing all volumes..."
    docker volume ls --format "{{.Name}}" | grep -E "scraping|grafana|prometheus|kafka|redis|postgres|zookeeper|delta" | while read vol; do
        docker volume rm "$vol" 2>/dev/null && print_info "Removed $vol" || true
    done

    print_step "Step 3: Cleaning up old images..."
    read -p "Remove and rebuild all images? (yes/no): " REBUILD_IMAGES
    if [ "$REBUILD_IMAGES" = "yes" ]; then
        docker images | grep "scraping" | awk '{print $3}' | xargs -r docker rmi -f 2>/dev/null || true
        print_info "Old images removed"
    fi

    print_step "Step 4: Verifying .env configuration..."
    if [ ! -f ".env" ]; then
        print_warning "Creating .env from .env.example..."
        cp .env.example .env
    fi

    # Ensure correct credentials
    if grep -q "GRAFANA_ADMIN_PASSWORD=" .env; then
        sed -i.bak 's/^GRAFANA_ADMIN_PASSWORD=.*/GRAFANA_ADMIN_PASSWORD=admin/' .env
    else
        echo "GRAFANA_ADMIN_PASSWORD=admin" >> .env
    fi

    if ! grep -q "^DB_PASSWORD=postgres" .env; then
        sed -i.bak 's/^DB_PASSWORD=.*/DB_PASSWORD=postgres/' .env || echo "DB_PASSWORD=postgres" >> .env
    fi

    print_info "Environment configuration verified"

    print_step "Step 5: Building Docker images..."
    if [ "$REBUILD_IMAGES" = "yes" ]; then
        docker-compose build --no-cache
    else
        docker-compose build
    fi
    print_info "Images built successfully"

    print_step "Step 6: Starting infrastructure..."
    print_info "Starting: Redis, PostgreSQL, Zookeeper, Kafka..."
    docker-compose up -d redis postgres zookeeper kafka

    print_info "Waiting 20 seconds for infrastructure to stabilize..."
    sleep 20

    print_step "Step 7: Starting monitoring stack..."
    print_info "Starting: Prometheus, Alertmanager, Grafana..."
    docker-compose up -d prometheus-a prometheus-b alertmanager-1 alertmanager-2 alertmanager-3 grafana

    sleep 10

    print_step "Step 8: Starting exporters..."
    docker-compose up -d redis-exporter postgres-exporter kafka-jmx-exporter statsd-exporter metrics-exporter

    sleep 5

    print_step "Step 9: Starting application services..."
    print_info "Starting: Scrapy, Stage workers, Kafka ingestor..."
    docker-compose up -d scrapy-app stage2-worker stage3-worker stage4-worker kafka-delta-ingestor

    print_info "Waiting for services to start..."
    sleep 10

    print_step "Step 10: Verifying deployment..."
    docker-compose ps

    print_section "Docker Services Health Check"

    # Health check function
    check_http() {
        local url=$1
        local name=$2
        local code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")
        if [ "$code" = "200" ] || [ "$code" = "302" ]; then
            print_info "${name}: Accessible (HTTP ${code})"
        else
            print_warning "${name}: Not ready yet (HTTP ${code})"
        fi
    }

    echo ""
    print_info "Testing service endpoints..."
    sleep 5
    check_http "http://localhost:3000" "Grafana"
    check_http "http://localhost:9091" "Prometheus A"
    check_http "http://localhost:9097" "Prometheus B"
    check_http "http://localhost:9090/metrics" "Metrics Exporter"

    print_section "Docker Rebuild Complete!"
    echo ""
    print_info "Access Points:"
    echo "  • Grafana:          ${CYAN}http://localhost:3000${NC} (admin/admin)"
    echo "  • Prometheus A:     ${CYAN}http://localhost:9091${NC}"
    echo "  • Prometheus B:     ${CYAN}http://localhost:9097${NC}"
    echo "  • Alertmanager:     ${CYAN}http://localhost:9093${NC}"
    echo ""
    print_info "Useful Commands:"
    echo "  • View logs:        ${CYAN}docker-compose logs -f${NC}"
    echo "  • Check status:     ${CYAN}docker-compose ps${NC}"
    echo "  • Stop all:         ${CYAN}docker-compose down${NC}"
    echo ""
fi

# ==========================================
# KUBERNETES REBUILD
# ==========================================
if [ "$MODE" = "k8s" ] || [ "$MODE" = "both" ]; then
    print_section "Kubernetes Rebuild"

    # Check prerequisites
    if ! command -v kubectl &> /dev/null; then
        print_error "kubectl not found. Install kubectl first."
        exit 1
    fi

    if ! command -v helm &> /dev/null; then
        print_error "helm not found. Install helm first."
        exit 1
    fi

    if ! kubectl cluster-info &> /dev/null; then
        print_error "Cannot connect to Kubernetes cluster."
        exit 1
    fi

    OLD_RELEASE="coco"
    NEW_RELEASE="scraping-pipeline"
    CHART_PATH="k8s/helm/scraping-pipeline"
    NAMESPACE="default"

    print_step "Step 1: Removing old 'coco' release..."
    if helm list -n "$NAMESPACE" | grep -q "^${OLD_RELEASE}"; then
        helm uninstall "$OLD_RELEASE" -n "$NAMESPACE" || true
        print_info "Old release uninstalled"
        sleep 10
    else
        print_warning "No 'coco' release found"
    fi

    print_step "Step 2: Cleaning up old resources..."

    # Remove PVCs
    kubectl delete pvc -l "app.kubernetes.io/instance=${OLD_RELEASE}" -n "$NAMESPACE" --wait=false 2>/dev/null || true

    # Remove ConfigMaps
    kubectl delete configmap -l "app.kubernetes.io/instance=${OLD_RELEASE}" -n "$NAMESPACE" 2>/dev/null || true

    # Remove Secrets
    kubectl delete secret -l "app.kubernetes.io/instance=${OLD_RELEASE}" -n "$NAMESPACE" 2>/dev/null || true

    # Force delete any stuck pods
    kubectl delete pods -l "app.kubernetes.io/instance=${OLD_RELEASE}" -n "$NAMESPACE" --force --grace-period=0 2>/dev/null || true

    print_info "Old resources cleaned up"
    sleep 5

    print_step "Step 3: Creating fresh secrets..."

    kubectl delete secret postgres-credentials -n "$NAMESPACE" 2>/dev/null || true
    kubectl delete secret grafana-credentials -n "$NAMESPACE" 2>/dev/null || true

    kubectl create secret generic postgres-credentials \
        --from-literal=password=postgres \
        --from-literal=DB_PASSWORD=postgres \
        -n "$NAMESPACE"

    kubectl create secret generic grafana-credentials \
        --from-literal=admin-user=admin \
        --from-literal=admin-password=admin \
        -n "$NAMESPACE"

    print_info "Secrets created (admin/admin for Grafana)"

    print_step "Step 4: Validating Helm chart..."
    if [ ! -d "$CHART_PATH" ]; then
        print_error "Chart not found at: $CHART_PATH"
        exit 1
    fi

    helm lint "$CHART_PATH" || print_warning "Chart has linting warnings"
    print_info "Chart validated"

    print_step "Step 5: Deploying new release..."
    print_info "Release name: ${NEW_RELEASE}"
    print_info "This may take 5-10 minutes..."

    helm install "$NEW_RELEASE" "$CHART_PATH" \
        --namespace "$NAMESPACE" \
        --create-namespace \
        --set secrets.grafana.adminUser=admin \
        --set secrets.grafana.adminPassword=admin \
        --set secrets.postgres.password=postgres \
        --timeout 15m \
        --wait || {
            print_error "Deployment failed. Check logs with: kubectl get pods -n ${NAMESPACE}"
            exit 1
        }

    print_info "Deployment complete"

    print_step "Step 6: Verifying deployment..."

    sleep 10

    echo ""
    echo "=== Pods ==="
    kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/instance=${NEW_RELEASE}"

    echo ""
    echo "=== Services ==="
    kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/instance=${NEW_RELEASE}"

    print_section "Kubernetes Rebuild Complete!"

    GRAFANA_SVC=$(kubectl get svc -n "$NAMESPACE" -l "app.kubernetes.io/component=grafana" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

    echo ""
    print_info "Service Naming:"
    echo "  ✗ OLD: coco-scraping-pipeline-* (removed)"
    echo "  ✓ NEW: scraping-pipeline-* (active)"
    echo ""

    if [ -n "$GRAFANA_SVC" ]; then
        print_info "To access Grafana:"
        echo "  ${CYAN}kubectl port-forward svc/${GRAFANA_SVC} 3000:3000 -n ${NAMESPACE}${NC}"
        echo "  Then visit: ${CYAN}http://localhost:3000${NC}"
        echo "  Login: ${CYAN}admin / admin${NC}"
    fi

    echo ""
    print_info "Useful Commands:"
    echo "  • View pods:        ${CYAN}kubectl get pods -n ${NAMESPACE}${NC}"
    echo "  • View logs:        ${CYAN}kubectl logs -f <pod-name> -n ${NAMESPACE}${NC}"
    echo "  • Helm status:      ${CYAN}helm status ${NEW_RELEASE} -n ${NAMESPACE}${NC}"
    echo "  • Delete:           ${CYAN}helm uninstall ${NEW_RELEASE} -n ${NAMESPACE}${NC}"
    echo ""
fi

# ==========================================
# FINAL SUMMARY
# ==========================================
print_section "Environment Rebuild Complete!"

echo ""
print_info "What was done:"
if [ "$MODE" = "docker" ] || [ "$MODE" = "both" ]; then
    echo "  ✓ Docker Compose: Full reset with all stages (including stage4)"
fi
if [ "$MODE" = "k8s" ] || [ "$MODE" = "both" ]; then
    echo "  ✓ Kubernetes: Removed 'coco' prefix, redeployed as 'scraping-pipeline'"
fi

echo ""
print_info "Default Credentials:"
echo "  • Grafana:    admin / admin"
echo "  • PostgreSQL: postgres / postgres"

echo ""
print_warning "Next Steps:"
echo "  1. Access Grafana and verify dashboards load"
echo "  2. Check datasources are connected (Prometheus, Redis, PostgreSQL)"
echo "  3. Monitor logs for any errors"
echo "  4. Verify pipeline stages are processing data"

echo ""
print_info "Troubleshooting:"
echo "  • Run diagnostics:  ${CYAN}./scripts/diagnose_issues.sh${NC}"
echo "  • View logs:        ${CYAN}docker-compose logs -f <service>${NC}"
echo "  • Check docs:       ${CYAN}./scripts/README.md${NC}"

echo ""
print_section "Happy Scraping! 🚀"
echo ""
