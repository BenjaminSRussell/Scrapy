#!/usr/bin/env bash
# Diagnostic script to check pipeline status (#403).
# Service names come from the active Compose file (scripts/compose_lib.sh),
# so nothing here assumes a service that `docker compose config --services`
# does not list.
# Diagnostic variant of the #822 standard (scripts/SHELL_STANDARD.md): no -e, so one
# failing probe is reported and the remaining checks still run.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
. scripts/compose_lib.sh

APP="$(compose_first scraper scrapy-app || true)"

echo "=========================================="
echo "Pipeline Diagnostic Report"
echo "=========================================="
echo ""
echo "Defined Compose services: $(compose_services | tr '\n' ' ')"
echo ""

echo "1. Docker Containers Status:"
compose ps --format "table {{.Name}}\t{{.Status}}" | head -20

echo ""
echo "2. Seed URLs in Delta Lake:"
if [ -n "$APP" ]; then
    compose exec -T "$APP" python -c "
from src.lakehouse.lakehouse_manager import LakehouseManager
dm = LakehouseManager.get_instance(start_workers=False)
print(f'  Seed URLs: {dm.count(\"seed_urls\")}')
" 2>/dev/null || echo "  ERROR: Could not check seed URLs (is '$APP' running?)"
else
    echo "  SKIP: no scraper service defined in this Compose file"
fi

echo ""
echo "3. Redis URL Hashes:"
if compose_has redis; then
    redis_count=$(compose exec -T redis redis-cli SCARD scrapy:url_hashes 2>/dev/null)
    echo "  Redis URL hashes: ${redis_count:-ERROR}"
else
    echo "  SKIP: no redis service defined"
fi

echo ""
echo "4. Recent logs per pipeline service (last 10 lines, INFO/ERROR):"
for svc in $(compose_filter "$APP" stage1-worker stage2-worker stage3-worker stage4-worker 2>/dev/null); do
    echo "  --- $svc"
    compose logs --tail=10 "$svc" 2>&1 | grep -E "INFO|ERROR|completed" || echo "  (no matching lines)"
done

echo ""
echo "5. Metrics endpoint (Prometheus):"
if compose_has prometheus; then
    code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:9090/-/ready 2>/dev/null || echo 000)
    echo "  prometheus /-/ready: HTTP ${code}"
else
    echo "  SKIP: no prometheus service defined"
fi

APP_HINT="${APP:-scraper}"
echo ""
echo "=========================================="
echo "Quick Actions:"
echo "=========================================="
echo "  Rebuild images:       docker compose build"
echo "  Reseed Delta Lake:    docker compose run --rm --no-deps ${APP_HINT} python reseed.py --force"
echo "  Restart scraper:      docker compose restart ${APP_HINT}"
echo "  View logs:            docker compose logs -f ${APP_HINT}"
echo "  Stop all:             docker compose down"
echo ""
