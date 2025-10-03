#!/bin/bash
#
# End-to-End test for the complete UConn scraping pipeline
#
# This test runs a minimal version of the full workflow:
# 1. Run Python scraper on a small set of test URLs
# 2. Trigger Java ETL loader on the output
# 3. Validate data in PostgreSQL database
#
# Expected environment variables:
#   DATABASE_URL - JDBC URL for PostgreSQL
#   DATABASE_USER - Database username
#   DATABASE_PASSWORD - Database password
#

set -e
set -u
set -o pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEST_RESULTS_DIR="$SCRIPT_DIR/results"
TEST_DATA_DIR="$SCRIPT_DIR/test_data"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[E2E]${NC} $1"; }
log_success() { echo -e "${GREEN}[E2E SUCCESS]${NC} $1"; }
log_error() { echo -e "${RED}[E2E ERROR]${NC} $1"; }

# Cleanup function
cleanup() {
    log_info "Cleaning up test data..."
    rm -rf "$TEST_DATA_DIR"
}
trap cleanup EXIT

# Create test directories
mkdir -p "$TEST_RESULTS_DIR"
mkdir -p "$TEST_DATA_DIR"

log_info "=========================================="
log_info "End-to-End Pipeline Test"
log_info "Started at: $(date)"
log_info "=========================================="

# Step 1: Create test URLs file
log_info "Step 1/5: Creating test URLs..."
cat > "$TEST_DATA_DIR/test_urls.txt" <<EOF
https://uconn.edu/
https://uconn.edu/about/
https://admissions.uconn.edu/
EOF

TEST_URL_COUNT=$(wc -l < "$TEST_DATA_DIR/test_urls.txt")
log_success "Created $TEST_URL_COUNT test URLs"

# Step 2: Run Python scraping pipeline (minimal)
log_info "Step 2/5: Running Python scraping pipeline..."

cd "$PROJECT_DIR"

# Create minimal test configuration
export SCRAPY_SETTINGS_MODULE='src.settings'
export UCONN_TEST_MODE='true'

# Run a minimal crawl (just a few pages)
python -m scrapy crawl enrichment_spider \
    -a start_urls_file="$TEST_DATA_DIR/test_urls.txt" \
    -s CLOSESPIDER_PAGECOUNT=5 \
    -s CONCURRENT_REQUESTS=2 \
    -s DOWNLOAD_DELAY=1 \
    -o "$TEST_DATA_DIR/enriched_test.jsonl" \
    2>&1 | tee "$TEST_RESULTS_DIR/scraping_${TIMESTAMP}.log"

# Verify output was created
if [ ! -f "$TEST_DATA_DIR/enriched_test.jsonl" ]; then
    log_error "Scraping did not produce output file"
    exit 1
fi

SCRAPED_COUNT=$(wc -l < "$TEST_DATA_DIR/enriched_test.jsonl")
if [ "$SCRAPED_COUNT" -eq 0 ]; then
    log_error "Scraping produced empty output"
    exit 1
fi

log_success "Scraped $SCRAPED_COUNT pages"

# Step 3: Validate scraped data structure
log_info "Step 3/5: Validating scraped data structure..."

python3 - <<EOF
import json
import sys

required_fields = ['url', 'title', 'entities', 'keywords', 'content_tags']
valid_records = 0

with open('$TEST_DATA_DIR/enriched_test.jsonl') as f:
    for line_num, line in enumerate(f, 1):
        try:
            record = json.loads(line)

            # Check required fields
            missing_fields = [f for f in required_fields if f not in record]
            if missing_fields:
                print(f"❌ Line {line_num}: Missing fields: {missing_fields}")
                sys.exit(1)

            # Validate data types
            if not isinstance(record['entities'], list):
                print(f"❌ Line {line_num}: 'entities' must be a list")
                sys.exit(1)

            if not isinstance(record['keywords'], list):
                print(f"❌ Line {line_num}: 'keywords' must be a list")
                sys.exit(1)

            valid_records += 1

        except json.JSONDecodeError as e:
            print(f"❌ Line {line_num}: Invalid JSON - {e}")
            sys.exit(1)

print(f"✓ Validated {valid_records} records")
sys.exit(0)
EOF

log_success "Data structure validation passed"

# Step 4: Run Java ETL loader
log_info "Step 4/5: Running Java ETL loader..."

JAR_PATH="$PROJECT_DIR/java-etl-loader/target/warehouse-etl-loader-1.0.0.jar"

if [ ! -f "$JAR_PATH" ]; then
    log_info "Building Java ETL loader..."
    cd "$PROJECT_DIR/java-etl-loader"
    mvn clean package -DskipTests
fi

# Run ETL loader
cd "$PROJECT_DIR/java-etl-loader"
java -jar "$JAR_PATH" \
    --spring.datasource.url="$DATABASE_URL" \
    --spring.datasource.username="$DATABASE_USER" \
    --spring.datasource.password="$DATABASE_PASSWORD" \
    --etl.input.directory="$TEST_DATA_DIR" \
    --etl.input.file-pattern="enriched_test.jsonl" \
    2>&1 | tee "$TEST_RESULTS_DIR/etl_${TIMESTAMP}.log"

log_success "ETL loader completed"

# Step 5: Validate data in PostgreSQL
log_info "Step 5/5: Validating data in PostgreSQL..."

# Extract connection details from JDBC URL
DB_HOST=$(echo "$DATABASE_URL" | sed -E 's|jdbc:postgresql://([^:/]+).*|\1|')
DB_PORT=$(echo "$DATABASE_URL" | sed -E 's|jdbc:postgresql://[^:]+:([0-9]+)/.*|\1|')
DB_NAME=$(echo "$DATABASE_URL" | sed -E 's|jdbc:postgresql://[^/]+/([^?]+).*|\1|')

export PGPASSWORD="$DATABASE_PASSWORD"

# Check pages table
PAGES_COUNT=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DATABASE_USER" -d "$DB_NAME" -tAc \
    "SELECT COUNT(*) FROM pages WHERE is_current = TRUE")

if [ "$PAGES_COUNT" -eq 0 ]; then
    log_error "No pages found in warehouse"
    exit 1
fi

log_success "Found $PAGES_COUNT pages in warehouse"

# Check entities table
ENTITIES_COUNT=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DATABASE_USER" -d "$DB_NAME" -tAc \
    "SELECT COUNT(*) FROM entities")

log_success "Found $ENTITIES_COUNT entities"

# Check keywords table
KEYWORDS_COUNT=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DATABASE_USER" -d "$DB_NAME" -tAc \
    "SELECT COUNT(*) FROM keywords")

log_success "Found $KEYWORDS_COUNT keywords"

# Check categories table
CATEGORIES_COUNT=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DATABASE_USER" -d "$DB_NAME" -tAc \
    "SELECT COUNT(*) FROM categories")

log_success "Found $CATEGORIES_COUNT categories"

# Verify relationships
ORPHAN_PAGES=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DATABASE_USER" -d "$DB_NAME" -tAc \
    "SELECT COUNT(*) FROM pages p WHERE p.is_current = TRUE AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.page_id = p.page_id)")

if [ "$ORPHAN_PAGES" -gt 0 ]; then
    log_error "Found $ORPHAN_PAGES pages without entities"
    exit 1
fi

log_success "All pages have entities (no orphans)"

# Generate test report
REPORT_FILE="$TEST_RESULTS_DIR/e2e_report_${TIMESTAMP}.json"
cat > "$REPORT_FILE" <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "test_results": {
    "status": "PASSED",
    "test_urls_count": $TEST_URL_COUNT,
    "scraped_pages_count": $SCRAPED_COUNT,
    "warehouse_stats": {
      "pages": $PAGES_COUNT,
      "entities": $ENTITIES_COUNT,
      "keywords": $KEYWORDS_COUNT,
      "categories": $CATEGORIES_COUNT,
      "orphan_pages": $ORPHAN_PAGES
    }
  }
}
EOF

log_success "Test report generated: $REPORT_FILE"

# Final summary
log_info "=========================================="
log_success "End-to-End Test PASSED!"
log_info "Summary:"
log_info "  - Test URLs: $TEST_URL_COUNT"
log_info "  - Scraped pages: $SCRAPED_COUNT"
log_info "  - Warehouse pages: $PAGES_COUNT"
log_info "  - Entities: $ENTITIES_COUNT"
log_info "  - Keywords: $KEYWORDS_COUNT"
log_info "  - Categories: $CATEGORIES_COUNT"
log_info "Completed at: $(date)"
log_info "=========================================="

exit 0
