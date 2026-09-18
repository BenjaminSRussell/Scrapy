#!/bin/bash
# STOP_PIPELINE.sh - Stop all pipeline processes

echo "🛑 Stopping Scraping Pipeline..."

PROJECT_DIR="$(dirname "$0")/.."
cd "$PROJECT_DIR" || exit 1

# Stop spider
if [ -f "logs/scout_spider.pid" ]; then
    PID=$(cat logs/scout_spider.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        kill "$PID" && echo "✅ Stopped Scout Spider (PID: $PID)"
    else
        echo "⚠️ Scout Spider (PID: $PID) not running"
    fi
    rm logs/scout_spider.pid
fi

# Stop metrics exporter
if [ -f "logs/metrics_exporter.pid" ]; then
    PID=$(cat logs/metrics_exporter.pid)
    if ps -p "$PID" > /dev/null 2>&1; then
        kill "$PID" && echo "✅ Stopped Metrics Exporter (PID: $PID)"
    else
        echo "⚠️ Metrics Exporter (PID: $PID) not running"
    fi
    rm logs/metrics_exporter.pid
fi

# Optionally stop Redis (commented out to keep it running)
# redis-cli shutdown

echo ""
echo "✅ Pipeline stopped"
echo "   Redis is still running (use 'redis-cli shutdown' to stop)"
