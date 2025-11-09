#!/bin/bash
# START_PIPELINE.sh - Start the scraping pipeline with available services
# This script starts what we CAN run without Docker

set -e

echo "========================================="
echo "🚀 STARTING SCRAPING PIPELINE"
echo "========================================="
date
echo ""

# Change to project directory
cd "$(dirname "$0")/.." || exit 1
PROJECT_DIR=$(pwd)
echo "📍 Project Directory: $PROJECT_DIR"
echo ""

# 1. Verify Redis is running
echo "1️⃣ Checking Redis..."
if redis-cli ping > /dev/null 2>&1; then
    echo "✅ Redis is running"
else
    echo "⚠️ Redis not running, starting it..."
    redis-server --daemonize yes --port 6379
    sleep 2
    if redis-cli ping > /dev/null 2>&1; then
        echo "✅ Redis started successfully"
    else
        echo "❌ Failed to start Redis"
        exit 1
    fi
fi
echo ""

# 2. Create necessary directories
echo "2️⃣ Setting up directories..."
mkdir -p data/delta_lake data/logs data/raw
mkdir -p logs
echo "✅ Directories ready"
echo ""

# 3. Start Prometheus metrics exporter
echo "3️⃣ Starting Prometheus metrics exporter..."
nohup python temp_scripts/metrics_exporter.py 9090 > logs/metrics_exporter.log 2>&1 &
METRICS_PID=$!
sleep 2
echo "✅ Metrics exporter started on port 9090 (PID: $METRICS_PID)"
echo "$METRICS_PID" > logs/metrics_exporter.pid
echo ""

# 4. Check for test URLs
echo "4️⃣ Checking for URL data..."
if [ -f "data/raw/uconn_urls.csv" ]; then
    URL_COUNT=$(wc -l < data/raw/uconn_urls.csv)
    echo "✅ Found $URL_COUNT URLs in uconn_urls.csv"
else
    echo "⚠️ No URL file found, will use spider's start_urls"
fi
echo ""

# 5. Run a test spider
echo "5️⃣ Starting Scrapy Scout Spider..."
echo "   Spider: ScoutSpider (Stage 1 - URL Discovery)"
echo "   Output: data/logs/scout_spider.log"
echo ""

# Set environment to use local config
export SCRAPY_SETTINGS_MODULE=src.settings
export PYTHONPATH="${PROJECT_DIR}:${PYTHONPATH}"

# Run spider using daemon wrapper to avoid event loop issues
nohup python temp_scripts/run_spider_daemon.py scout 100 \
    > logs/scrapy_stdout.log 2>&1 &

SPIDER_PID=$!
echo "✅ Scout spider started (PID: $SPIDER_PID)"
echo "$SPIDER_PID" > logs/scout_spider.pid
echo ""

# 6. Monitor the pipeline
echo "6️⃣ Pipeline is now RUNNING!"
echo "========================================="
echo ""
echo "📊 MONITORING ENDPOINTS:"
echo "  • Prometheus Metrics: http://localhost:9090/metrics"
echo "  • Redis: localhost:6379"
echo ""
echo "📁 LOG FILES:"
echo "  • Spider Log: data/logs/scout_spider.log"
echo "  • Stdout Log: logs/scrapy_stdout.log"
echo ""
echo "🔍 RUNNING PROCESSES:"
echo "  • Metrics Exporter: PID $METRICS_PID"
echo "  • Scout Spider: PID $SPIDER_PID"
echo ""
echo "========================================="
echo "💡 TO MONITOR PROGRESS:"
echo "  tail -f data/logs/scout_spider.log"
echo "  tail -f logs/scrapy_stdout.log"
echo ""
echo "💡 TO STOP PIPELINE:"
echo "  bash temp_scripts/STOP_PIPELINE.sh"
echo ""
echo "💡 TO CHECK STATUS:"
echo "  bash temp_scripts/CHECK_PIPELINE_STATUS.sh"
echo "========================================="
