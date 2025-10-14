#!/bin/bash
# ==================================================================
# Diagnostic Script - Identify Issues in the Stack
# ==================================================================
# This script checks for common issues with Grafana, Prometheus,
# Kafka, and pipeline stages
# ==================================================================

set -e

echo "=========================================="
echo "  Stack Diagnostic Tool"
echo "=========================================="
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
print_section() { echo -e "\n${BLUE}=== $1 ===${NC}"; }

# Detect environment
if [ -f "docker-compose.yml" ] && command -v docker-compose &> /dev/null; then
    ENV_TYPE="docker"
    print_info "Environment: Docker Compose"
elif command -v kubectl &> /dev/null && kubectl cluster-info &> /dev/null; then
    ENV_TYPE="kubernetes"
    print_info "Environment: Kubernetes"
else
    print_error "Could not detect environment"
    exit 1
fi

# ==========================================
# Docker Compose Diagnostics
# ==========================================
if [ "$ENV_TYPE" = "docker" ]; then

    print_section "Docker Services Status"
    docker-compose ps

    print_section "Health Checks"

    # Check critical services
    SERVICES=("redis" "postgres" "zookeeper" "kafka" "prometheus-a" "grafana")

    for service in "${SERVICES[@]}"; do
        if docker-compose ps | grep "$service" | grep -q "Up"; then
            if docker-compose ps | grep "$service" | grep -q "healthy"; then
                print_ok "${service}: Running and Healthy"
            else
                print_warning "${service}: Running but not healthy"
            fi
        else
            print_error "${service}: Not running"
        fi
    done

    print_section "Environment Configuration"

    if [ -f ".env" ]; then
        print_ok ".env file exists"

        if grep -q "^GRAFANA_ADMIN_PASSWORD=admin" .env; then
            print_ok "Grafana password set to 'admin'"
        else
            print_warning "Grafana password not set to 'admin'"
            echo "  Current value: $(grep GRAFANA_ADMIN_PASSWORD .env || echo 'NOT SET')"
        fi

        if grep -q "^DB_PASSWORD=" .env && ! grep -q "^DB_PASSWORD=$" .env; then
            print_ok "Database password is set"
        else
            print_error "Database password not set in .env"
        fi
    else
        print_error ".env file not found"
    fi

    print_section "Service Connectivity Tests"

    # Test HTTP endpoints
    test_endpoint() {
        local url=$1
        local name=$2
        local code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")

        if [ "$code" = "200" ] || [ "$code" = "302" ]; then
            print_ok "${name}: Accessible (HTTP ${code})"
        else
            print_error "${name}: Not accessible (HTTP ${code})"
        fi
    }

    test_endpoint "http://localhost:3000" "Grafana"
    test_endpoint "http://localhost:9091" "Prometheus A"
    test_endpoint "http://localhost:9097" "Prometheus B"
    test_endpoint "http://localhost:6379" "Redis"
    test_endpoint "http://localhost:9090/metrics" "Metrics Exporter"

    print_section "Grafana Configuration"

    # Check Grafana datasources
    if docker-compose ps | grep grafana | grep -q "Up"; then
        print_info "Checking Grafana datasources..."

        DATASOURCES=$(curl -s -u admin:admin http://localhost:3000/api/datasources 2>/dev/null || echo "[]")

        if echo "$DATASOURCES" | grep -q "prometheus"; then
            print_ok "Prometheus datasource configured"
        else
            print_warning "Prometheus datasource not found"
        fi

        if echo "$DATASOURCES" | grep -q "postgres"; then
            print_ok "PostgreSQL datasource configured"
        else
            print_warning "PostgreSQL datasource not found"
        fi
    fi

    print_section "Pipeline Stages Status"

    STAGE_SERVICES=("scrapy-app" "stage2-worker" "stage3-worker" "stage4-worker")

    for stage in "${STAGE_SERVICES[@]}"; do
        if docker-compose ps | grep "$stage" | grep -q "Up"; then
            print_ok "${stage}: Running"

            # Check logs for errors
            ERROR_COUNT=$(docker-compose logs --tail=50 "$stage" 2>/dev/null | grep -i "error\|exception\|failed" | wc -l || echo "0")
            if [ "$ERROR_COUNT" -gt 0 ]; then
                print_warning "${stage}: Found ${ERROR_COUNT} errors in recent logs"
            fi
        else
            print_error "${stage}: Not running"
        fi
    done

    print_section "Kafka Status"

    if docker-compose ps | grep kafka | grep -q "Up"; then
        print_ok "Kafka broker is running"

        # Test Kafka connectivity
        print_info "Testing Kafka connectivity..."
        docker-compose exec -T kafka kafka-topics --bootstrap-server localhost:9092 --list &>/dev/null && \
            print_ok "Kafka is responsive" || \
            print_error "Kafka is not responsive"

        # List topics
        print_info "Kafka topics:"
        docker-compose exec -T kafka kafka-topics --bootstrap-server localhost:9092 --list 2>/dev/null || \
            print_warning "Could not list Kafka topics"
    else
        print_error "Kafka broker not running"
    fi

    print_section "Volume Status"

    VOLUMES=$(docker volume ls --format "{{.Name}}" | grep -i "scraping\|grafana\|prometheus" || echo "")

    if [ -n "$VOLUMES" ]; then
        print_info "Found volumes:"
        echo "$VOLUMES" | while read vol; do
            echo "  • $vol"
        done
    else
        print_warning "No volumes found"
    fi

    print_section "Recent Error Logs"

    print_info "Checking for recent errors in all services..."
    docker-compose logs --tail=100 2>/dev/null | grep -i "error\|exception\|failed\|fatal" | tail -20 || \
        print_ok "No recent errors found"

# ==========================================
# Kubernetes Diagnostics
# ==========================================
elif [ "$ENV_TYPE" = "kubernetes" ]; then

    NAMESPACE="${NAMESPACE:-default}"

    print_section "Kubernetes Resources"

    print_info "Checking for 'coco' prefix resources..."
    COCO_RESOURCES=$(kubectl get all -n "$NAMESPACE" 2>/dev/null | grep "coco-scraping-pipeline" | wc -l || echo "0")

    if [ "$COCO_RESOURCES" -gt 0 ]; then
        print_warning "Found ${COCO_RESOURCES} resources with 'coco-scraping-pipeline-*' naming"
        print_warning "This needs to be cleaned up!"
        echo ""
        kubectl get all -n "$NAMESPACE" | grep "coco-scraping-pipeline" | head -10
        echo ""
        print_info "Run: ./scripts/k8s_reset_and_deploy.sh to fix this"
    else
        print_ok "No 'coco' prefix resources found"
    fi

    print_section "Helm Releases"

    if command -v helm &> /dev/null; then
        helm list -n "$NAMESPACE"

        if helm list -n "$NAMESPACE" | grep -q "coco"; then
            print_warning "Found 'coco' Helm release - should be removed"
        fi
    else
        print_warning "Helm not installed"
    fi

    print_section "Pod Status"

    kubectl get pods -n "$NAMESPACE" -o wide 2>/dev/null || print_error "Could not get pods"

    print_info "Checking pod health..."
    TOTAL_PODS=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l || echo "0")
    RUNNING_PODS=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep "Running" | wc -l || echo "0")

    print_info "Pods: ${RUNNING_PODS}/${TOTAL_PODS} running"

    print_section "Service Status"

    kubectl get svc -n "$NAMESPACE" 2>/dev/null || print_error "Could not get services"

    print_section "Secrets Status"

    print_info "Checking required secrets..."

    if kubectl get secret grafana-credentials -n "$NAMESPACE" &>/dev/null; then
        print_ok "grafana-credentials secret exists"
    else
        print_error "grafana-credentials secret missing"
    fi

    if kubectl get secret postgres-credentials -n "$NAMESPACE" &>/dev/null; then
        print_ok "postgres-credentials secret exists"
    else
        print_error "postgres-credentials secret missing"
    fi

    print_section "Recent Pod Errors"

    print_info "Checking pod logs for errors..."
    kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | awk '{print $1}' | while read pod; do
        ERROR_COUNT=$(kubectl logs "$pod" -n "$NAMESPACE" --tail=50 2>/dev/null | grep -i "error\|exception\|failed" | wc -l || echo "0")
        if [ "$ERROR_COUNT" -gt 5 ]; then
            print_warning "${pod}: ${ERROR_COUNT} errors in recent logs"
        fi
    done

fi

# ==========================================
# Common Checks
# ==========================================

print_section "Configuration Files"

if [ -f "monitoring/grafana_datasource.yml" ]; then
    print_ok "Grafana datasource config exists"
else
    print_error "Grafana datasource config missing"
fi

if [ -f "monitoring/prometheus.yml" ]; then
    print_ok "Prometheus config exists"
else
    print_error "Prometheus config missing"
fi

if [ -f "docker-compose.yml" ]; then
    # Check if stage4 is in docker-compose
    if grep -q "stage4-worker" docker-compose.yml; then
        print_ok "Stage 4 worker defined in docker-compose.yml"
    else
        print_warning "Stage 4 worker NOT defined in docker-compose.yml"
    fi
fi

print_section "Summary"
echo ""
print_info "Diagnostic scan complete!"
echo ""

if [ "$ENV_TYPE" = "docker" ]; then
    print_info "Recommended actions for Docker:"
    echo "  1. If Grafana has issues: ./scripts/reset_grafana_complete.sh"
    echo "  2. For complete reset:    ./scripts/complete_reset.sh"
    echo "  3. View logs:             docker-compose logs -f <service-name>"
elif [ "$ENV_TYPE" = "kubernetes" ]; then
    print_info "Recommended actions for Kubernetes:"
    echo "  1. Remove 'coco' and redeploy: ./scripts/k8s_reset_and_deploy.sh"
    echo "  2. View logs:                   kubectl logs -f <pod-name>"
    echo "  3. Port forward Grafana:        kubectl port-forward svc/<grafana-svc> 3000:3000"
fi

echo ""
