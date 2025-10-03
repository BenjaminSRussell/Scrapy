#!/bin/bash
#
# Simple shell-based workflow orchestration for the UConn scraping pipeline.
# This is a lightweight alternative to Airflow for environments that don't need
# the full complexity of a workflow scheduler.
#
# Usage: ./run_pipeline.sh [--no-java] [--validate-only]
#

set -e  # Exit on error
set -u  # Exit on undefined variable
set -o pipefail  # Exit on pipe failure

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ENRICHED_DATA_DIR="$PROJECT_DIR/data/enriched"
JAVA_ETL_JAR="$PROJECT_DIR/java-etl-loader/target/warehouse-etl-loader-1.0.0.jar"
LOG_DIR="$PROJECT_DIR/data/logs"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Database configuration (can be overridden by environment variables)
DATABASE_URL="${DATABASE_URL:-jdbc:postgresql://localhost:5432/uconn_warehouse}"
DATABASE_USER="${DATABASE_USER:-postgres}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:-}"

# Flags
RUN_JAVA=true
VALIDATE_ONLY=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-java)
            RUN_JAVA=false
            shift
            ;;
        --validate-only)
            VALIDATE_ONLY=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--no-java] [--validate-only]"
            exit 1
            ;;
    esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Create log directory
mkdir -p "$LOG_DIR"

log_info "==================================="
log_info "UConn Scraping Pipeline Orchestrator"
log_info "Started at: $(date)"
log_info "==================================="

# Step 1: Run Python scraping pipeline
if [ "$VALIDATE_ONLY" = false ]; then
    log_info "Step 1/7: Running Stage 1 (Discovery Spider)..."
    cd "$PROJECT_DIR"

    python -m scrapy crawl discovery_spider \
        -s CLOSESPIDER_PAGECOUNT=1000 \
        -s CONCURRENT_REQUESTS=16 \
        2>&1 | tee "$LOG_DIR/stage1_${TIMESTAMP}.log"

    if [ ${PIPESTATUS[0]} -ne 0 ]; then
        log_error "Stage 1 failed!"
        exit 1
    fi
    log_success "Stage 1 completed"

    # Step 2: Validation
    log_info "Step 2/7: Running Stage 2 (Validation)..."
    python src/stage2/validator.py 2>&1 | tee "$LOG_DIR/stage2_${TIMESTAMP}.log"

    if [ ${PIPESTATUS[0]} -ne 0 ]; then
        log_error "Stage 2 failed!"
        exit 1
    fi
    log_success "Stage 2 completed"

    # Step 3: Enrichment
    log_info "Step 3/7: Running Stage 3 (Enrichment)..."
    python -m scrapy crawl enrichment_spider \
        -s CONCURRENT_REQUESTS=8 \
        2>&1 | tee "$LOG_DIR/stage3_${TIMESTAMP}.log"

    if [ ${PIPESTATUS[0]} -ne 0 ]; then
        log_error "Stage 3 failed!"
        exit 1
    fi
    log_success "Stage 3 completed"
fi

# Step 4: Validate scraped data
log_info "Step 4/7: Validating scraped data..."

ENRICHED_FILES=$(find "$ENRICHED_DATA_DIR" -name "*.jsonl" -type f 2>/dev/null | wc -l)
if [ "$ENRICHED_FILES" -eq 0 ]; then
    log_error "No enriched data files found in $ENRICHED_DATA_DIR"
    exit 1
fi

TOTAL_RECORDS=0
for file in "$ENRICHED_DATA_DIR"/*.jsonl; do
    if [ -f "$file" ]; then
        COUNT=$(wc -l < "$file")
        TOTAL_RECORDS=$((TOTAL_RECORDS + COUNT))
    fi
done

if [ "$TOTAL_RECORDS" -eq 0 ]; then
    log_error "Scraped files contain no records"
    exit 1
fi

log_success "Validation passed: $TOTAL_RECORDS records across $ENRICHED_FILES files"

# Step 5: Run Java ETL loader (if enabled)
if [ "$RUN_JAVA" = true ] && [ "$VALIDATE_ONLY" = false ]; then
    log_info "Step 5/7: Running Java ETL loader..."

    if [ ! -f "$JAVA_ETL_JAR" ]; then
        log_warning "Java ETL JAR not found at $JAVA_ETL_JAR"
        log_warning "Building JAR..."
        cd "$PROJECT_DIR/java-etl-loader"
        mvn clean package -DskipTests
    fi

    cd "$PROJECT_DIR/java-etl-loader"
    java -jar "$JAVA_ETL_JAR" \
        --spring.datasource.url="$DATABASE_URL" \
        --spring.datasource.username="$DATABASE_USER" \
        --spring.datasource.password="$DATABASE_PASSWORD" \
        --etl.input.directory="$ENRICHED_DATA_DIR" \
        2>&1 | tee "$LOG_DIR/java_etl_${TIMESTAMP}.log"

    if [ ${PIPESTATUS[0]} -ne 0 ]; then
        log_error "Java ETL loader failed!"
        exit 1
    fi
    log_success "Java ETL loader completed"

    # Step 6: Validate warehouse data
    log_info "Step 6/7: Validating warehouse data..."

    # Simple validation using psql
    CURRENT_PAGES=$(PGPASSWORD="$DATABASE_PASSWORD" psql -h localhost -U "$DATABASE_USER" -d uconn_warehouse -tAc \
        "SELECT COUNT(*) FROM pages WHERE is_current = TRUE" 2>/dev/null || echo "0")

    if [ "$CURRENT_PAGES" -eq 0 ]; then
        log_error "No current pages found in warehouse"
        exit 1
    fi

    log_success "Warehouse validation passed: $CURRENT_PAGES current pages"

    # Step 7: Generate metrics report
    log_info "Step 7/7: Generating metrics report..."

    REPORT_FILE="$PROJECT_DIR/data/reports/metrics_${TIMESTAMP}.json"
    mkdir -p "$(dirname "$REPORT_FILE")"

    cat > "$REPORT_FILE" <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "pipeline_run": {
    "total_records_scraped": $TOTAL_RECORDS,
    "enriched_files": $ENRICHED_FILES,
    "warehouse_current_pages": $CURRENT_PAGES
  },
  "status": "success"
}
EOF

    log_success "Metrics report generated: $REPORT_FILE"
else
    log_info "Skipping Java ETL loader (--no-java flag set or --validate-only mode)"
fi

# Final summary
log_info "==================================="
log_success "Pipeline completed successfully!"
log_info "Completed at: $(date)"
log_info "==================================="

exit 0
