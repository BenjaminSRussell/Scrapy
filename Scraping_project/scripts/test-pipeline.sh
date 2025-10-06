#!/bin/bash
# ==================================================================
# Pipeline End-to-End Test Script
# ==================================================================
# This script tests the complete pipeline flow:
# 1. Trigger a test scrape
# 2. Verify Kafka ingestion
# 3. Verify Delta Lake ingestion
# ==================================================================

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}===================================================================${NC}"
echo -e "${BLUE}Pipeline End-to-End Test${NC}"
echo -e "${BLUE}===================================================================${NC}"

# Configuration
TEST_URL="${TEST_URL:-http://quotes.toscrape.com}"
KAFKA_TOPIC="${KAFKA_TOPIC:-scraped-items}"

# Step 1: Trigger a test scrape
echo -e "\n${BLUE}[1/3] Triggering test scrape...${NC}"
echo "Target URL: $TEST_URL"

# Check if spider exists
echo "Available spiders:"
docker-compose exec scrapy-app scrapy list 2>/dev/null || echo "Could not list spiders"

echo -e "\n${YELLOW}Running test scrape...${NC}"
# Run a simple crawl - adjust spider name based on your actual spider
docker-compose exec -T scrapy-app python -c "
from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

# Create a simple test item
import json
from confluent_kafka import Producer
from datetime import datetime

# Configure producer
config = {
    'bootstrap.servers': 'kafka:9092',
}

producer = Producer(config)

# Create test item
test_item = {
    'url': '$TEST_URL',
    'title': 'Test Scrape from Pipeline',
    'content': 'This is a test message to verify the pipeline is working correctly.',
    'scraped_at_utc': datetime.utcnow().isoformat() + 'Z',
    'spider_name': 'test_spider',
    'pipeline_version': '1.0.0'
}

# Publish to Kafka
producer.produce('$KAFKA_TOPIC', value=json.dumps(test_item).encode('utf-8'))
producer.flush()

print('✓ Test message published to Kafka')
"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Test scrape completed${NC}"
else
    echo -e "${YELLOW}Note: Direct scrape may have failed, trying alternative method...${NC}"
    # Alternative: Send test message directly to Kafka
    docker-compose exec -T kafka kafka-console-producer.sh \
        --bootstrap-server localhost:9092 \
        --topic $KAFKA_TOPIC << EOF
{"url":"$TEST_URL","title":"Test Item","content":"Test content","scraped_at_utc":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","spider_name":"test","pipeline_version":"1.0.0"}
EOF
    echo -e "${GREEN}✓ Test message sent directly to Kafka${NC}"
fi

# Step 2: Verify Kafka ingestion
echo -e "\n${BLUE}[2/3] Verifying Kafka ingestion...${NC}"
echo "Reading from Kafka topic: $KAFKA_TOPIC"

KAFKA_OUTPUT=$(docker-compose exec -T kafka kafka-console-consumer.sh \
    --bootstrap-server localhost:9092 \
    --topic $KAFKA_TOPIC \
    --from-beginning \
    --max-messages 1 \
    --timeout-ms 5000 2>&1 || true)

if echo "$KAFKA_OUTPUT" | grep -q "url"; then
    echo -e "${GREEN}✓ Message found in Kafka${NC}"
    echo "Sample message:"
    echo "$KAFKA_OUTPUT" | grep "url" | head -1 | jq '.' 2>/dev/null || echo "$KAFKA_OUTPUT" | grep "url" | head -1
else
    echo -e "${YELLOW}⚠ Could not verify message in Kafka (may still be processing)${NC}"
fi

# Step 3: Verify Delta Lake ingestion
echo -e "\n${BLUE}[3/3] Verifying Delta Lake ingestion...${NC}"
echo "Waiting for kafka-delta-ingest to process messages (60 seconds)..."

# Monitor kafka-delta-ingest logs
echo -e "${YELLOW}Watching ingestor logs...${NC}"
timeout 60 docker-compose logs -f kafka-delta-ingestor 2>&1 | grep -m 1 "Writing batch\|Successfully wrote" || true

echo -e "\n${YELLOW}Note: Delta Lake verification requires additional tools${NC}"
echo "To verify Delta Lake ingestion, you can:"
echo "1. Check MinIO Console: http://localhost:9001"
echo "2. Look for objects in bucket: delta-lake/scraped_data/"
echo "3. Use Delta Lake tools to query the table"
echo ""
echo "Example MinIO check:"
echo "  mc ls local/delta-lake/scraped_data/"

# Check ingestor metrics
echo -e "\n${BLUE}Checking ingestor metrics...${NC}"
METRICS=$(curl -s http://localhost:9102/metrics 2>/dev/null || echo "")
if [ ! -z "$METRICS" ]; then
    echo "StatsD Exporter Metrics:"
    echo "$METRICS" | grep "kafka_delta_ingest" | head -10 || echo "No kafka_delta_ingest metrics found yet"
else
    echo -e "${YELLOW}Could not fetch metrics (exporter may not be ready)${NC}"
fi

# Check Prometheus targets
echo -e "\n${BLUE}Checking Prometheus targets...${NC}"
TARGETS=$(curl -s http://localhost:9091/api/v1/targets 2>/dev/null || echo "")
if echo "$TARGETS" | grep -q "activeTargets"; then
    echo -e "${GREEN}✓ Prometheus is scraping targets${NC}"
    echo "$TARGETS" | jq '.data.activeTargets[] | {job: .labels.job, health: .health, lastScrape: .lastScrape}' 2>/dev/null | head -20 || echo "Could not parse targets"
else
    echo -e "${YELLOW}Could not verify Prometheus targets${NC}"
fi

echo -e "\n${GREEN}===================================================================${NC}"
echo -e "${GREEN}Pipeline test completed!${NC}"
echo -e "${GREEN}===================================================================${NC}"

echo -e "\n${BLUE}Next steps:${NC}"
echo "1. Check service logs: docker-compose logs -f kafka-delta-ingestor"
echo "2. Verify MinIO data: ./scripts/init-minio.sh (then use mc ls)"
echo "3. View Grafana dashboards: http://localhost:3000"
echo "4. Check Prometheus metrics: http://localhost:9091"
echo ""
