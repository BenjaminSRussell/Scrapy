#!/bin/bash
# ==================================================================
# Complete Stack Reset and Rebuild Script
# ==================================================================
# This script performs a complete reset of the entire scraping pipeline:
# 1. Stops all services
# 2. Removes all volumes and data
# 3. Rebuilds images
# 4. Restarts with clean configuration
# ==================================================================

set -e

echo "=========================================="
echo "  Complete Stack Reset and Rebuild"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

# Ask for confirmation
echo ""
print_warning "This will DELETE ALL DATA and rebuild the entire stack!"
read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    print_info "Aborted by user"
    exit 0
fi

echo ""
print_step "Step 1: Stopping all services..."
echo "=========================================="
docker-compose down || print_warning "Some services may not be running"

echo ""
print_step "Step 2: Removing all volumes..."
echo "=========================================="
print_info "Removing Docker volumes..."

# List of volumes to remove
VOLUMES=(
    "redis_data"
    "postgres_data"
    "zookeeper_data"
    "zookeeper_logs"
    "kafka_data"
    "delta_data"
    "prometheus_a_data"
    "prometheus_b_data"
    "alertmanager_1_data"
    "alertmanager_2_data"
    "alertmanager_3_data"
    "grafana_data"
)

for vol in "${VOLUMES[@]}"; do
    # Try different volume name patterns
    docker volume rm "scraping_project_${vol}" 2>/dev/null || \
    docker volume rm "scraping-project_${vol}" 2>/dev/null || \
    docker volume rm "scraping_${vol}" 2>/dev/null || \
    print_warning "Volume ${vol} not found (may already be deleted)"
done

echo ""
print_step "Step 3: Removing old images (optional)..."
echo "=========================================="
read -p "Remove and rebuild Docker images? (yes/no): " REBUILD_IMAGES

if [ "$REBUILD_IMAGES" = "yes" ]; then
    print_info "Removing old images..."
    docker-compose rm -f || true
    docker images | grep "scraping" | awk '{print $3}' | xargs -r docker rmi -f || print_warning "No images to remove"
fi

echo ""
print_step "Step 4: Verifying .env configuration..."
echo "=========================================="

if [ ! -f ".env" ]; then
    print_warning ".env file not found, creating from example..."
    cp .env.example .env
fi

# Ensure admin/admin credentials
print_info "Setting Grafana credentials to admin/admin..."
if grep -q "GRAFANA_ADMIN_PASSWORD=" .env; then
    sed -i.bak 's/^GRAFANA_ADMIN_PASSWORD=.*/GRAFANA_ADMIN_PASSWORD=admin/' .env
else
    echo "GRAFANA_ADMIN_PASSWORD=admin" >> .env
fi

# Verify database password is set
if ! grep -q "^DB_PASSWORD=.*" .env || grep -q "^DB_PASSWORD=$" .env; then
    print_warning "DB_PASSWORD not set in .env, using default 'postgres'"
    if grep -q "DB_PASSWORD=" .env; then
        sed -i.bak 's/^DB_PASSWORD=.*/DB_PASSWORD=postgres/' .env
    else
        echo "DB_PASSWORD=postgres" >> .env
    fi
fi

print_info "Environment configuration verified"
cat .env

echo ""
print_step "Step 5: Building Docker images..."
echo "=========================================="

if [ "$REBUILD_IMAGES" = "yes" ]; then
    print_info "Building all images from scratch..."
    docker-compose build --no-cache
else
    print_info "Building images (using cache)..."
    docker-compose build
fi

echo ""
print_step "Step 6: Starting infrastructure services..."
echo "=========================================="
print_info "Starting core infrastructure (Redis, PostgreSQL, Kafka)..."

docker-compose up -d redis postgres zookeeper kafka

print_info "Waiting for infrastructure to be healthy..."
sleep 15

# Check health
for service in redis postgres kafka; do
    print_info "Checking ${service}..."
    for i in {1..30}; do
        if docker-compose ps | grep "$service" | grep -q "healthy\|Up"; then
            print_info "${service} is ready!"
            break
        fi
        echo -n "."
        sleep 2
    done
    echo ""
done

echo ""
print_step "Step 7: Starting monitoring stack..."
echo "=========================================="
print_info "Starting Prometheus, Alertmanager, and Grafana..."

docker-compose up -d prometheus-a prometheus-b alertmanager-1 alertmanager-2 alertmanager-3 grafana

print_info "Waiting for monitoring services to be ready..."
sleep 10

echo ""
print_step "Step 8: Starting exporters..."
echo "=========================================="
print_info "Starting metrics exporters..."

docker-compose up -d redis-exporter postgres-exporter kafka-jmx-exporter statsd-exporter metrics-exporter

sleep 5

echo ""
print_step "Step 9: Starting application services..."
echo "=========================================="
print_info "Starting Scrapy and pipeline workers..."

docker-compose up -d scrapy-app stage2-worker stage3-worker stage4-worker kafka-delta-ingestor

print_info "Waiting for services to start..."
sleep 10

echo ""
print_step "Step 10: Verifying stack status..."
echo "=========================================="

print_info "Checking all services..."
docker-compose ps

echo ""
print_step "Step 11: Checking service health..."
echo "=========================================="

# Function to check HTTP endpoint
check_endpoint() {
    local url=$1
    local name=$2
    local http_code=$(curl -s -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "000")

    if [ "$http_code" = "200" ] || [ "$http_code" = "302" ]; then
        print_info "✓ ${name} is accessible (HTTP ${http_code})"
        return 0
    else
        print_warning "✗ ${name} may not be ready (HTTP ${http_code})"
        return 1
    fi
}

print_info "Testing service endpoints..."
check_endpoint "http://localhost:3000" "Grafana"
check_endpoint "http://localhost:9091" "Prometheus A"
check_endpoint "http://localhost:9097" "Prometheus B"
check_endpoint "http://localhost:9093" "Alertmanager 1"
check_endpoint "http://localhost:9090/metrics" "Metrics Exporter"

echo ""
echo "=========================================="
echo "  Reset Complete!"
echo "=========================================="
echo ""
print_info "Access Points:"
echo "  • Grafana:          http://localhost:3000 (admin/admin)"
echo "  • Prometheus A:     http://localhost:9091"
echo "  • Prometheus B:     http://localhost:9097"
echo "  • Alertmanager 1:   http://localhost:9093"
echo "  • Metrics Exporter: http://localhost:9090/metrics"
echo ""
print_info "Useful Commands:"
echo "  • View all logs:        docker-compose logs -f"
echo "  • View specific logs:   docker-compose logs -f grafana"
echo "  • Check status:         docker-compose ps"
echo "  • Stop all:             docker-compose down"
echo "  • Restart service:      docker-compose restart <service-name>"
echo ""
print_warning "Next Steps:"
echo "  1. Login to Grafana at http://localhost:3000"
echo "  2. Verify datasources are connected"
echo "  3. Check that dashboards are loading"
echo "  4. Monitor logs for any errors"
echo ""
