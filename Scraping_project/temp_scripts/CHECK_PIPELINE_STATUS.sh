#!/bin/bash
# CHECK_PIPELINE_STATUS.sh - Check pipeline runtime status

echo "========================================="
echo "🔍 PIPELINE STATUS CHECK"
echo "========================================="
date
echo ""

PROJECT_DIR="$(dirname "$0")/.."
cd "$PROJECT_DIR" || exit 1

# Check Redis
echo "1️⃣ REDIS STATUS"
if redis-cli ping > /dev/null 2>&1; then
    echo "  ✅ Redis is RUNNING"
    echo "  📊 Keys: $(redis-cli dbsize | awk '{print $2}')"
else
    echo "  ❌ Redis is NOT running"
fi
echo ""

# Check Metrics Exporter
echo "2️⃣ METRICS EXPORTER"
if [ -f "logs/metrics_exporter.pid" ]; then
    PID=$(cat logs/metrics_exporter.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "  ✅ RUNNING (PID: $PID)"
        echo "  🌐 http://localhost:9090/metrics"
        # Test endpoint
        if curl -s http://localhost:9090/metrics | head -1 > /dev/null 2>&1; then
            echo "  ✅ Endpoint responding"
        else
            echo "  ⚠️ Endpoint not responding"
        fi
    else
        echo "  ❌ NOT running (stale PID: $PID)"
    fi
else
    echo "  ❌ NOT started"
fi
echo ""

# Check Spider
echo "3️⃣ SCRAPY SPIDER"
if [ -f "logs/scout_spider.pid" ]; then
    PID=$(cat logs/scout_spider.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        echo "  ✅ RUNNING (PID: $PID)"
    else
        echo "  ❌ NOT running (stale PID: $PID)"
    fi
else
    echo "  ❌ NOT started"
fi
echo ""

# Check Log Files
echo "4️⃣ LOG FILES"
if [ -f "data/logs/scout_spider.log" ]; then
    LINES=$(wc -l < data/logs/scout_spider.log)
    echo "  📄 scout_spider.log: $LINES lines"
    echo "     Latest entries:"
    tail -3 data/logs/scout_spider.log | sed 's/^/     /'
else
    echo "  ⚠️ No spider log yet"
fi
echo ""

# Check Data Output
echo "5️⃣ DATA OUTPUT"
if [ -d "data/delta_lake" ]; then
    SIZE=$(du -sh data/delta_lake 2>/dev/null | cut -f1)
    FILE_COUNT=$(find data/delta_lake -type f 2>/dev/null | wc -l)
    echo "  📦 Delta Lake: $SIZE ($FILE_COUNT files)"
else
    echo "  ⚠️ No delta lake data yet"
fi
echo ""

# Process Summary
echo "========================================="
echo "📊 ACTIVE PROCESSES"
echo "========================================="
ps aux | grep -E "(scrapy|prometheus|redis-server)" | grep -v grep | awk '{print "  PID " $2 ": " $11 " " $12 " " $13}' || echo "  No pipeline processes found"
echo ""
echo "========================================="
