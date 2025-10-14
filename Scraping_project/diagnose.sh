#!/bin/bash
# Diagnostic script to check pipeline status

echo "=========================================="
echo "Pipeline Diagnostic Report"
echo "=========================================="
echo ""

echo "1. Docker Containers Status:"
docker-compose ps --format "table {{.Name}}\t{{.Status}}" | head -20

echo ""
echo "2. Seed URLs in Docker Volume:"
docker-compose exec -T scrapy-app python -c "
from src.common.delta_lake import DeltaLakeManager
dm = DeltaLakeManager.get_instance(start_workers=False)
count = dm.count('seed_urls')
print(f'  Seed URLs: {count}')
" 2>/dev/null || echo "  ERROR: Could not check seed URLs"

echo ""
echo "3. Redis URL Hashes:"
redis_count=$(docker-compose exec -T redis redis-cli SCARD scrapy:url_hashes 2>/dev/null)
echo "  Redis URL hashes: ${redis_count:-ERROR}"

echo ""
echo "4. Recent Scrapy App Logs (last 10 lines):"
docker-compose logs --tail=10 scrapy-app 2>&1 | grep -E "INFO|ERROR|completed"

echo ""
echo "5. Metrics Exporter Status:"
docker-compose logs --tail=5 metrics-exporter 2>&1 | grep -E "INFO|ERROR"

echo ""
echo "=========================================="
echo "Quick Actions:"
echo "=========================================="
echo "  Rebuild images:      docker-compose build"
echo "  Reseed Docker volume: docker-compose run --rm --no-deps scrapy-app python reseed.py --force"
echo "  Restart scrapers:    docker-compose restart scrapy-app"
echo "  View logs:           docker-compose logs -f scrapy-app"
echo "  Stop all:            docker-compose down"
echo ""
