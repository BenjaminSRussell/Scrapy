#!/bin/bash
# ==================================================================
# Kafka Real-Time Metrics Verification Script
# ==================================================================
# This script verifies that Kafka metrics are flowing to Prometheus
# in real-time
# ==================================================================

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}===================================================================${NC}"
echo -e "${BLUE}Kafka Real-Time Metrics Verification${NC}"
echo -e "${BLUE}===================================================================${NC}"

# Step 1: Verify Kafka JMX is enabled and accessible
echo -e "\n${BLUE}[1/6] Verifying Kafka JMX is enabled...${NC}"
JMX_CHECK=$(docker-compose exec -T kafka bash -c "echo 'stats' | nc localhost 9999" 2>&1 || echo "")
if [ ! -z "$JMX_CHECK" ]; then
    echo -e "${GREEN}✓ Kafka JMX is accessible on port 9999${NC}"
else
    echo -e "${RED}✗ Kafka JMX is not accessible${NC}"
    echo "Checking Kafka JMX configuration..."
    docker-compose exec kafka env | grep JMX
fi

# Step 2: Verify JMX Exporter is running and connected to Kafka
echo -e "\n${BLUE}[2/6] Verifying JMX Exporter is running...${NC}"
if docker-compose ps kafka-jmx-exporter | grep -q "Up"; then
    echo -e "${GREEN}✓ JMX Exporter container is running${NC}"

    # Check if it's exposing metrics
    echo "Checking JMX Exporter metrics endpoint..."
    sleep 2
    JMX_METRICS=$(curl -s http://localhost:5556/metrics 2>&1 || echo "")
    if echo "$JMX_METRICS" | grep -q "kafka_"; then
        echo -e "${GREEN}✓ JMX Exporter is exposing Kafka metrics${NC}"
        echo "Sample metrics:"
        echo "$JMX_METRICS" | grep "kafka_server_brokertopicmetrics" | head -5
    else
        echo -e "${YELLOW}⚠ JMX Exporter is running but not exposing Kafka metrics yet${NC}"
        echo "This may take a few moments after startup..."
    fi
else
    echo -e "${RED}✗ JMX Exporter is not running${NC}"
    docker-compose ps kafka-jmx-exporter
fi

# Step 3: Verify Prometheus is scraping JMX Exporter
echo -e "\n${BLUE}[3/6] Verifying Prometheus is scraping Kafka JMX metrics...${NC}"
PROM_TARGETS=$(curl -s http://localhost:9091/api/v1/targets 2>&1 || echo "")
if echo "$PROM_TARGETS" | grep -q "kafka_jmx"; then
    # Check if target is up
    TARGET_HEALTH=$(echo "$PROM_TARGETS" | jq -r '.data.activeTargets[] | select(.labels.job=="kafka_jmx") | .health' 2>/dev/null || echo "unknown")
    if [ "$TARGET_HEALTH" = "up" ]; then
        echo -e "${GREEN}✓ Prometheus is successfully scraping Kafka JMX metrics${NC}"
        LAST_SCRAPE=$(echo "$PROM_TARGETS" | jq -r '.data.activeTargets[] | select(.labels.job=="kafka_jmx") | .lastScrape' 2>/dev/null || echo "")
        echo "Last scrape: $LAST_SCRAPE"
    else
        echo -e "${RED}✗ Prometheus target 'kafka_jmx' is $TARGET_HEALTH${NC}"
        echo "Target details:"
        echo "$PROM_TARGETS" | jq '.data.activeTargets[] | select(.labels.job=="kafka_jmx")' 2>/dev/null || echo "Could not parse target info"
    fi
else
    echo -e "${RED}✗ Prometheus is not configured to scrape kafka_jmx${NC}"
fi

# Step 4: Test real-time metrics by sending data to Kafka
echo -e "\n${BLUE}[4/6] Testing real-time metrics flow...${NC}"
echo "Sending test messages to Kafka..."

# Get baseline message count
BASELINE=$(curl -s 'http://localhost:9091/api/v1/query?query=kafka_server_brokertopicmetrics_messagesin_total{topic="scraped-items"}' 2>/dev/null | jq -r '.data.result[0].value[1]' 2>/dev/null || echo "0")
echo "Baseline message count: $BASELINE"

# Send 5 test messages
for i in {1..5}; do
    docker-compose exec -T kafka kafka-console-producer.sh \
        --bootstrap-server localhost:9092 \
        --topic scraped-items << EOF
{"url":"http://test-$i.com","title":"Test $i","content":"Test content","scraped_at_utc":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","spider_name":"test","pipeline_version":"1.0.0"}
EOF
    echo "  Sent message $i/5"
done

echo -e "${YELLOW}Waiting 15 seconds for metrics to propagate...${NC}"
sleep 15

# Check new message count
NEW_COUNT=$(curl -s 'http://localhost:9091/api/v1/query?query=kafka_server_brokertopicmetrics_messagesin_total{topic="scraped-items"}' 2>/dev/null | jq -r '.data.result[0].value[1]' 2>/dev/null || echo "0")
echo "New message count: $NEW_COUNT"

if (( $(echo "$NEW_COUNT > $BASELINE" | bc -l 2>/dev/null || echo "0") )); then
    DIFF=$(echo "$NEW_COUNT - $BASELINE" | bc -l 2>/dev/null || echo "0")
    echo -e "${GREEN}✓ Real-time metrics are working! Detected $DIFF new messages${NC}"
else
    echo -e "${YELLOW}⚠ Metrics not updated yet. This may indicate a scraping delay.${NC}"
fi

# Step 5: Query key Kafka metrics from Prometheus
echo -e "\n${BLUE}[5/6] Querying key Kafka metrics from Prometheus...${NC}"

echo -e "\n${YELLOW}Messages In (per topic):${NC}"
curl -s 'http://localhost:9091/api/v1/query?query=kafka_server_brokertopicmetrics_messagesin_total' 2>/dev/null | \
    jq -r '.data.result[] | "\(.metric.topic): \(.value[1])"' 2>/dev/null || \
    echo "Could not query metrics"

echo -e "\n${YELLOW}Bytes In (per topic):${NC}"
curl -s 'http://localhost:9091/api/v1/query?query=kafka_server_brokertopicmetrics_bytesin_total' 2>/dev/null | \
    jq -r '.data.result[] | "\(.metric.topic): \(.value[1])"' 2>/dev/null | head -5 || \
    echo "Could not query metrics"

echo -e "\n${YELLOW}Active Controller Count (should be 1):${NC}"
curl -s 'http://localhost:9091/api/v1/query?query=kafka_controller_activecontrollercount' 2>/dev/null | \
    jq -r '.data.result[0].value[1]' 2>/dev/null || \
    echo "Could not query metrics"

echo -e "\n${YELLOW}Under-Replicated Partitions (should be 0):${NC}"
curl -s 'http://localhost:9091/api/v1/query?query=kafka_server_replicamanager_underreplicatedpartitions' 2>/dev/null | \
    jq -r '.data.result[0].value[1]' 2>/dev/null || \
    echo "Could not query metrics"

# Step 6: Verify StatsD metrics from kafka-delta-ingest
echo -e "\n${BLUE}[6/6] Verifying kafka-delta-ingest metrics via StatsD...${NC}"
STATSD_METRICS=$(curl -s http://localhost:9102/metrics 2>&1 || echo "")
if echo "$STATSD_METRICS" | grep -q "kafka_delta_ingest"; then
    echo -e "${GREEN}✓ kafka-delta-ingest is sending metrics to StatsD exporter${NC}"
    echo "Sample metrics:"
    echo "$STATSD_METRICS" | grep "kafka_delta_ingest" | head -5
else
    echo -e "${YELLOW}⚠ No kafka-delta-ingest metrics found yet${NC}"
    echo "This is normal if the ingestor hasn't processed messages yet"
fi

# Summary
echo -e "\n${GREEN}===================================================================${NC}"
echo -e "${GREEN}Verification Summary${NC}"
echo -e "${GREEN}===================================================================${NC}"

echo -e "\n${BLUE}Real-Time Metrics Flow:${NC}"
echo "  Kafka Broker ──[JMX:9999]──▶ JMX Exporter:5556 ──[HTTP]──▶ Prometheus:9091"
echo "  kafka-delta-ingest ──[StatsD:UDP]──▶ StatsD Exporter:9102 ──[HTTP]──▶ Prometheus:9091"

echo -e "\n${BLUE}Verification URLs:${NC}"
echo "  - Kafka JMX Metrics:     http://localhost:5556/metrics"
echo "  - StatsD Metrics:        http://localhost:9102/metrics"
echo "  - Prometheus Targets:    http://localhost:9091/targets"
echo "  - Prometheus Graph:      http://localhost:9091/graph"

echo -e "\n${BLUE}Example Prometheus Queries:${NC}"
echo "  # Message rate (messages/sec)"
echo "  rate(kafka_server_brokertopicmetrics_messagesin_total[1m])"
echo ""
echo "  # Byte rate (bytes/sec)"
echo "  rate(kafka_server_brokertopicmetrics_bytesin_total[1m])"
echo ""
echo "  # Consumer lag"
echo "  kafka_consumer_records_lag"
echo ""
echo "  # Delta Lake ingest rate"
echo "  rate(kafka_delta_ingest_messages_received[1m])"

echo -e "\n${BLUE}Next Steps:${NC}"
echo "1. Open Grafana: http://localhost:3000"
echo "2. Create dashboard with above queries"
echo "3. Set up alerts for critical metrics"
echo "4. Monitor in real-time during production scraping"

echo ""
