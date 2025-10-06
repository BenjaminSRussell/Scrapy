#!/bin/bash
# ==================================================================
# Complete Pipeline Startup Script
# ==================================================================
# This script starts the entire scraping pipeline infrastructure
# ==================================================================

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}===================================================================${NC}"
echo -e "${BLUE}Starting Complete Scraping Pipeline${NC}"
echo -e "${BLUE}===================================================================${NC}"

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${YELLOW}Warning: .env file not found. Creating from .env.example...${NC}"
    cp .env.example .env
    echo -e "${GREEN}✓ Created .env file. Please review and update if needed.${NC}"
fi

# Step 1: Start infrastructure services
echo -e "\n${BLUE}[1/6] Starting infrastructure services (Redis, PostgreSQL, Zookeeper, Kafka)...${NC}"
docker-compose up -d redis postgres zookeeper kafka

echo "Waiting for services to be healthy..."
sleep 10

# Step 2: Start MinIO
echo -e "\n${BLUE}[2/6] Starting MinIO (S3-compatible storage)...${NC}"
docker-compose up -d minio

echo "Waiting for MinIO to be ready..."
sleep 5

# Step 3: Initialize MinIO buckets
echo -e "\n${BLUE}[3/6] Initializing MinIO buckets...${NC}"
./scripts/init-minio.sh

# Step 4: Start monitoring stack
echo -e "\n${BLUE}[4/6] Starting monitoring stack (Prometheus, Alertmanager, Grafana)...${NC}"
docker-compose up -d prometheus-a prometheus-b alertmanager-1 alertmanager-2 alertmanager-3 grafana

# Step 5: Start metrics exporters
echo -e "\n${BLUE}[5/6] Starting metrics exporters...${NC}"
docker-compose up -d redis-exporter postgres-exporter kafka-jmx-exporter statsd-exporter metrics-exporter

# Step 6: Start application services
echo -e "\n${BLUE}[6/6] Starting application services (Scrapy, Kafka-Delta-Ingest)...${NC}"
docker-compose up -d scrapy-app kafka-delta-ingestor

echo -e "\n${GREEN}===================================================================${NC}"
echo -e "${GREEN}Pipeline startup complete!${NC}"
echo -e "${GREEN}===================================================================${NC}"

echo -e "\n${BLUE}Service URLs:${NC}"
echo "  - MinIO Console:      http://localhost:9001 (minioadmin / minioadmin123)"
echo "  - Grafana:            http://localhost:3000 (admin / admin)"
echo "  - Prometheus A:       http://localhost:9091"
echo "  - Prometheus B:       http://localhost:9092"
echo "  - Alertmanager 1:     http://localhost:9093"
echo "  - Alertmanager 2:     http://localhost:9095"
echo "  - Alertmanager 3:     http://localhost:9096"
echo "  - Scrapy Metrics:     http://localhost:9410/metrics"

echo -e "\n${BLUE}Useful Commands:${NC}"
echo "  - View logs:          docker-compose logs -f [service-name]"
echo "  - Check status:       docker-compose ps"
echo "  - Stop all:           docker-compose down"
echo "  - Test pipeline:      ./scripts/test-pipeline.sh"

echo -e "\n${YELLOW}Checking service health...${NC}"
docker-compose ps

echo ""
