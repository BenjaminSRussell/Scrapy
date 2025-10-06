#!/bin/bash

# ==================================================================
# UConn Scraping Pipeline - One-Command Startup Script
# ==================================================================
# This script sets up and starts the entire pipeline infrastructure
# ==================================================================

set -e  # Exit on error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# Functions
print_header() {
    echo -e "${BLUE}"
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║  UConn Web Scraping Pipeline - Tier 1 Architecture            ║"
    echo "║  Complete Infrastructure Startup                               ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${CYAN}ℹ${NC} $1"
}

check_prerequisite() {
    if command -v $1 &> /dev/null; then
        print_success "$2 found"
        return 0
    else
        print_error "$2 not found"
        return 1
    fi
}

# Start
print_header

echo -e "${BLUE}[1/5] Checking Prerequisites${NC}"
echo "─────────────────────────────────────────────────────────────────"

MISSING_DEPS=0

# Check Python
if check_prerequisite python3 "Python 3"; then
    VERSION=$(python3 --version | cut -d' ' -f2)
    print_info "Version: $VERSION"
else
    MISSING_DEPS=1
fi

# Check Docker
if ! check_prerequisite docker "Docker"; then
    MISSING_DEPS=1
fi

# Check Docker Compose
if ! check_prerequisite docker-compose "Docker Compose"; then
    MISSING_DEPS=1
fi

if [ $MISSING_DEPS -eq 1 ]; then
    echo ""
    print_error "Missing required dependencies. Please install and try again."
    echo ""
    echo "Installation instructions:"
    echo "  • Python 3.10+: https://www.python.org/downloads/"
    echo "  • Docker: https://docs.docker.com/get-docker/"
    echo "  • Docker Compose: https://docs.docker.com/compose/install/"
    exit 1
fi

echo ""
echo -e "${BLUE}[2/5] Setting Up Python Environment${NC}"
echo "─────────────────────────────────────────────────────────────────"

# Create virtual environment
if [ ! -d ".venv" ]; then
    print_info "Creating virtual environment..."
    python3 -m venv .venv
    print_success "Virtual environment created"
else
    print_success "Virtual environment exists"
fi

# Activate virtual environment
print_info "Activating virtual environment..."
source .venv/bin/activate
print_success "Virtual environment activated"

# Install dependencies
print_info "Installing/updating dependencies..."
pip install --upgrade pip -q
pip install -e . -q
print_success "Dependencies installed"

echo ""
echo -e "${BLUE}[3/5] Starting Docker Infrastructure${NC}"
echo "─────────────────────────────────────────────────────────────────"

# Check if Docker is running
if ! docker info &> /dev/null; then
    print_error "Docker is not running. Please start Docker Desktop and try again."
    exit 1
fi

print_info "Starting services with Docker Compose..."
docker-compose up -d

# Wait for services to be healthy
print_info "Waiting for services to be ready..."
sleep 5

# Check service health
SERVICES=(redis postgres prometheus grafana)
ALL_HEALTHY=1

for service in "${SERVICES[@]}"; do
    if docker-compose ps | grep $service | grep -q "Up"; then
        print_success "$service is running"
    else
        print_warning "$service may not be running correctly"
        ALL_HEALTHY=0
    fi
done

echo ""
echo -e "${BLUE}[4/5] Service Endpoints${NC}"
echo "─────────────────────────────────────────────────────────────────"
echo ""
echo "  🔴 Redis:        localhost:6379"
echo "  🐘 PostgreSQL:   localhost:5432 (user: postgres, pass: postgres)"
echo "  📈 Prometheus:   http://localhost:9090"
echo "  📊 Grafana:      http://localhost:3000 (user: admin, pass: admin)"
echo ""

echo -e "${BLUE}[5/5] System Status${NC}"
echo "─────────────────────────────────────────────────────────────────"

# Test Redis connection
if redis-cli -h localhost -p 6379 ping &> /dev/null; then
    print_success "Redis connection verified"
else
    print_warning "Redis connection could not be verified"
fi

# List queues
print_info "Current queue status:"
python drain_lake.py --list

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗"
echo "║  Infrastructure Started Successfully! 🎉                       ║"
echo "╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${CYAN}Next Steps:${NC}"
echo ""
echo "  ${YELLOW}Option A:${NC} Run monolithic pipeline (simpler, all-in-one)"
echo "    python run_pipeline.py run"
echo ""
echo "  ${YELLOW}Option B:${NC} Run distributed pipeline (production-grade)"
echo "    Terminal 1: python monitoring/metrics_exporter.py"
echo "    Terminal 2: python src/consumers/delta_consumer.py --all"
echo "    Terminal 3: cd src/stage1 && scrapy crawl scout"
echo "    Terminal 4: python src/stage2/stage2_worker.py"
echo ""
echo "  ${YELLOW}Management:${NC}"
echo "    • Check health:   python run_pipeline.py health"
echo "    • Manage queues:  python drain_lake.py --list"
echo "    • View logs:      docker-compose logs -f"
echo "    • Stop services:  docker-compose down"
echo ""
echo "  ${YELLOW}Documentation:${NC}"
echo "    • README.md - General overview"
echo "    • TIER1_UPGRADE_GUIDE.md - Detailed architecture guide"
echo "    • IMPLEMENTATION_SUMMARY.md - What's new"
echo ""
echo -e "${GREEN}Happy Scraping! 🚀${NC}"
echo ""
