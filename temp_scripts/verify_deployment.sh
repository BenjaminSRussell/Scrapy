#!/bin/bash
# Deployment Verification Script
set -e
echo "🔍 DEPLOYMENT VERIFICATION"
echo "=================================="

check_service() {
    local service=$1
    echo -n "Checking $service... "
    if docker-compose ps | grep -q "$service.*Up.*healthy\|$service.*Up"; then
        echo "✅ Running"
        return 0
    else
        echo "❌ Not running"
        return 1
    fi
}

docker info >/dev/null 2>&1 && echo "✅ Docker is running" || exit 1
check_service "redis"
check_service "postgres"
check_service "kafka"
check_service "scrapy-app"
echo "=================================="
echo "✅ DEPLOYMENT VERIFIED"
