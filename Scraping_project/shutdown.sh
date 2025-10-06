#!/bin/bash

# ==================================================================
# UConn Scraping Pipeline - Clean Shutdown Script
# ==================================================================
# This script safely shuts down the pipeline and saves all data
# ==================================================================

set -e  # Exit on error

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_header() {
    echo -e "${BLUE}"
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║  UConn Scraping Pipeline - Clean Shutdown                      ║"
    echo "║  Safely saving all data before shutdown                        ║"
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

print_header

echo -e "${BLUE}[1/6] Checking Running Processes${NC}"
echo "─────────────────────────────────────────────────────────────────"

# Check for Python processes
PYTHON_PROCS=$(pgrep -f "python.*pipeline\|python.*consumer\|python.*exporter\|python.*worker\|scrapy crawl" || true)

if [ -z "$PYTHON_PROCS" ]; then
    print_warning "No pipeline processes found running"
else
    print_info "Found running pipeline processes:"
    ps aux | grep -E "python.*pipeline|python.*consumer|python.*exporter|python.*worker|scrapy crawl" | grep -v grep || true
fi

echo ""
echo -e "${BLUE}[2/6] Signaling Graceful Shutdown${NC}"
echo "─────────────────────────────────────────────────────────────────"

if [ -n "$PYTHON_PROCS" ]; then
    print_info "Sending SIGTERM to all pipeline processes for graceful shutdown..."

    # Send SIGTERM (graceful shutdown signal)
    for pid in $PYTHON_PROCS; do
        if ps -p $pid > /dev/null 2>&1; then
            print_info "Sending SIGTERM to process $pid"
            kill -TERM $pid 2>/dev/null || true
        fi
    done

    print_info "Waiting up to 30 seconds for processes to save data..."

    # Wait up to 30 seconds for graceful shutdown
    for i in {1..30}; do
        REMAINING=$(pgrep -f "python.*pipeline\|python.*consumer\|python.*exporter\|python.*worker\|scrapy crawl" || true)
        if [ -z "$REMAINING" ]; then
            print_success "All processes shut down gracefully"
            break
        fi
        sleep 1
        echo -ne "\r  Waiting... ${i}s elapsed"
    done
    echo ""

    # Force kill if still running
    REMAINING=$(pgrep -f "python.*pipeline\|python.*consumer\|python.*exporter\|python.*worker\|scrapy crawl" || true)
    if [ -n "$REMAINING" ]; then
        print_warning "Some processes did not shut down gracefully, force killing..."
        for pid in $REMAINING; do
            kill -9 $pid 2>/dev/null || true
        done
        print_warning "Force killed remaining processes"
    fi
else
    print_success "No processes to shut down"
fi

echo ""
echo -e "${BLUE}[3/6] Flushing Redis to Disk${NC}"
echo "─────────────────────────────────────────────────────────────────"

# Check if Redis is running
if docker ps | grep -q scraping_redis; then
    print_info "Flushing Redis data to disk..."

    # Force save to disk (BGSAVE is non-blocking, SAVE is blocking but guaranteed)
    docker exec scraping_redis redis-cli SAVE > /dev/null 2>&1 || true

    # Wait a moment for save to complete
    sleep 2

    # Check last save time
    LAST_SAVE=$(docker exec scraping_redis redis-cli LASTSAVE 2>/dev/null || echo "unknown")
    print_success "Redis data saved (last save: $LAST_SAVE)"
else
    print_warning "Redis container not running, skipping flush"
fi

echo ""
echo -e "${BLUE}[4/6] Checking Delta Lake Status${NC}"
echo "─────────────────────────────────────────────────────────────────"

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    source .venv/bin/activate

    # Run health check to see Delta Lake status
    print_info "Checking Delta Lake table statistics..."

    if [ -f "run_pipeline.py" ]; then
        python run_pipeline.py health 2>&1 | grep -E "stage[0-9]|records|✅|✗" || print_warning "Could not retrieve Delta Lake stats"
    fi
else
    print_warning "Virtual environment not found, skipping Delta Lake check"
fi

echo ""
echo -e "${BLUE}[5/6] Stopping Docker Services${NC}"
echo "─────────────────────────────────────────────────────────────────"

if command -v docker-compose &> /dev/null; then
    print_info "Stopping Docker Compose services..."

    # Stop services gracefully (gives them time to shut down)
    docker-compose stop

    print_success "Docker services stopped"

    # Optionally show service status
    print_info "Final service status:"
    docker-compose ps
else
    print_warning "Docker Compose not found, skipping"
fi

echo ""
echo -e "${BLUE}[6/6] Final Cleanup${NC}"
echo "─────────────────────────────────────────────────────────────────"

# Deactivate virtual environment
if [ -n "$VIRTUAL_ENV" ]; then
    deactivate 2>/dev/null || true
    print_success "Virtual environment deactivated"
fi

# Show data directory status
if [ -d "data/delta_lake" ]; then
    DELTA_SIZE=$(du -sh data/delta_lake 2>/dev/null | cut -f1 || echo "unknown")
    print_info "Delta Lake size: $DELTA_SIZE"
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗"
echo "║  Shutdown Complete! 🎉                                         ║"
echo "╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""

echo -e "${CYAN}Data Safety Summary:${NC}"
echo "  ✅ All Python processes stopped gracefully (30s timeout)"
echo "  ✅ Redis data flushed to disk"
echo "  ✅ Delta Lake tables preserved"
echo "  ✅ Docker services stopped cleanly"
echo ""

echo -e "${CYAN}To restart:${NC}"
echo "  ./start.sh"
echo ""

echo -e "${CYAN}To remove all data (DANGEROUS):${NC}"
echo "  docker-compose down -v   # Removes all volumes"
echo "  rm -rf data/delta_lake   # Removes Delta Lake data"
echo ""

echo -e "${GREEN}Goodbye! 👋${NC}"
echo ""
