#!/bin/bash

# ==================================================================
# Emergency Delta Lake Lock Cleanup Script
# ==================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_header() {
    echo -e "${RED}"
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║  EMERGENCY DELTA LAKE CLEANUP                                  ║"
    echo "║  Use only when system is locked/hanging                        ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

print_info() {
    echo -e "${CYAN}ℹ${NC} $1"
}

print_header

echo -e "${BLUE}[1/5] Killing Zombie Processes${NC}"
echo "─────────────────────────────────────────────────────────────────"

# Find and kill all Python processes related to the pipeline
ZOMBIE_PROCS=$(pgrep -f "python.*run_pipeline\|python.*cli.py\|scrapy crawl" 2>/dev/null || true)

if [ -n "$ZOMBIE_PROCS" ]; then
    print_warning "Found zombie processes: $ZOMBIE_PROCS"

    for pid in $ZOMBIE_PROCS; do
        if ps -p $pid > /dev/null 2>&1; then
            print_info "Killing process $pid"
            kill -9 $pid 2>/dev/null || print_warning "Could not kill $pid"
        fi
    done

    sleep 2
    print_success "Zombie processes killed"
else
    print_success "No zombie processes found"
fi

echo ""
echo -e "${BLUE}[2/5] Checking for Open File Handles${NC}"
echo "─────────────────────────────────────────────────────────────────"

if command -v lsof &> /dev/null; then
    OPEN_HANDLES=$(lsof +D data/delta_lake 2>/dev/null | wc -l)

    if [ "$OPEN_HANDLES" -gt 1 ]; then
        print_warning "Found $OPEN_HANDLES open file handles on Delta Lake"
        print_info "Processes with open handles:"
        lsof +D data/delta_lake 2>/dev/null | awk 'NR>1 {print $1, $2}' | sort -u || true
    else
        print_success "No processes holding Delta Lake files"
    fi
else
    print_warning "lsof not available, skipping file handle check"
fi

echo ""
echo -e "${BLUE}[3/5] Checking Delta Transaction Logs${NC}"
echo "─────────────────────────────────────────────────────────────────"

for table_dir in data/delta_lake/*/; do
    table_name=$(basename "$table_dir")

    if [ -d "$table_dir/_delta_log" ]; then
        log_count=$(find "$table_dir/_delta_log" -name "*.json" -o -name "*.parquet" 2>/dev/null | wc -l)

        if [ "$log_count" -gt 1000 ]; then
            echo -e "${RED}✗${NC} $table_name: $log_count log files (CRITICAL - needs VACUUM)"
        elif [ "$log_count" -gt 500 ]; then
            echo -e "${YELLOW}⚠${NC} $table_name: $log_count log files (needs optimization)"
        else
            print_success "$table_name: $log_count log files (healthy)"
        fi
    fi
done

echo ""
echo -e "${BLUE}[4/5] Stopping Docker Containers${NC}"
echo "─────────────────────────────────────────────────────────────────"

if command -v docker &> /dev/null; then
    # Force stop containers (no graceful shutdown)
    print_info "Force stopping all containers..."
    docker-compose down --timeout 5 2>&1 || docker-compose kill 2>&1 || true

    print_success "Docker containers stopped"
else
    print_warning "Docker not available"
fi

echo ""
echo -e "${BLUE}[5/5] Recommendations${NC}"
echo "─────────────────────────────────────────────────────────────────"

print_info "To fix lock contention issues permanently:"
echo ""
echo "  1. Run VACUUM regularly to clean up old transaction logs:"
echo "     ${CYAN}python scripts/vacuum_delta_tables.py${NC}"
echo ""
echo "  2. Run OPTIMIZE to compact small files:"
echo "     ${CYAN}python cli.py optimize${NC}"
echo ""
echo "  3. Set up automatic maintenance (add to cron):"
echo "     ${CYAN}0 2 * * * cd /path/to/project && python scripts/vacuum_delta_tables.py${NC}"
echo ""

echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗"
echo "║  Emergency Cleanup Complete! ✅                                ║"
echo "╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
