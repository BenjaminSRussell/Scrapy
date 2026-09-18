#!/bin/bash
# Complete System Status Check - Shows what's actually running

echo "========================================="
echo "🔍 COMPLETE SYSTEM STATUS CHECK"
echo "========================================="
date

echo ""
echo "📍 Current Directory: $(pwd)"
echo ""

# 1. Check Dependencies Installation
echo "1️⃣ PYTHON DEPENDENCIES"
echo "-----------------------------------------"
if command -v python &> /dev/null; then
    echo "✅ Python: $(python --version)"
    
    # Check key packages
    for pkg in scrapy pydantic redis psycopg2 pyarrow deltalake; do
        if python -c "import $pkg" 2>/dev/null; then
            version=$(python -c "import $pkg; print($pkg.__version__)" 2>/dev/null || echo "unknown")
            echo "  ✅ $pkg ($version)"
        else
            echo "  ❌ $pkg (not installed)"
        fi
    done
else
    echo "❌ Python not found"
fi

# 2. Check Docker
echo ""
echo "2️⃣ DOCKER STATUS"
echo "-----------------------------------------"
if command -v docker &> /dev/null; then
    echo "✅ Docker: $(docker --version)"
    if docker info &> /dev/null; then
        echo "✅ Docker daemon is running"
        
        # Check running containers
        container_count=$(docker ps -q | wc -l)
        echo "📦 Running containers: $container_count"
        
        if [ $container_count -gt 0 ]; then
            echo ""
            docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | head -20
        fi
    else
        echo "❌ Docker daemon not running"
    fi
    
    if command -v docker-compose &> /dev/null; then
        echo "✅ Docker Compose: $(docker-compose --version)"
    else
        echo "❌ Docker Compose not installed"
    fi
else
    echo "❌ Docker not installed"
fi

# 3. Check File Structure
echo ""
echo "3️⃣ FILE STRUCTURE"
echo "-----------------------------------------"
files_check=(
    "config.yml:Configuration"
    "docker-compose.yml:Docker Compose"
    "test_pipeline_10k.py:10K Test Suite"
    "PRODUCTION_READY.md:Production Guide"
    "DOCKER_DEPLOYMENT_GUIDE.md:Deployment Guide"
    ".env.example:Environment Template"
)

for item in "${files_check[@]}"; do
    file="${item%%:*}"
    desc="${item##*:}"
    if [ -f "$file" ]; then
        echo "  ✅ $desc ($file)"
    else
        echo "  ❌ $desc ($file) - MISSING"
    fi
done

# 4. Check Data Directories
echo ""
echo "4️⃣ DATA DIRECTORIES"
echo "-----------------------------------------"
for dir in data/delta_lake data/logs data/raw; do
    if [ -d "$dir" ]; then
        size=$(du -sh "$dir" 2>/dev/null | cut -f1)
        echo "  ✅ $dir ($size)"
    else
        echo "  ⚠️ $dir (will be created)"
        mkdir -p "$dir"
    fi
done

# 5. Check Services Status
echo ""
echo "5️⃣ SERVICES STATUS"
echo "-----------------------------------------"
services=(
    "http://localhost:3001:Grafana"
    "http://localhost:9091:Prometheus-A"
    "http://localhost:9090/metrics:Metrics"
    "http://localhost:6379:Redis"
)

for item in "${services[@]}"; do
    url="${item%%:*}"
    name="${item##*:}"
    if curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 "$url" | grep -q "200\|302\|000"; then
        status_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 "$url")
        if [ "$status_code" != "000" ]; then
            echo "  ✅ $name - HTTP $status_code"
        else
            echo "  ❌ $name - Not responding"
        fi
    else
        echo "  ❌ $name - Not accessible"
    fi
done

# 6. System Resources
echo ""
echo "6️⃣ SYSTEM RESOURCES"
echo "-----------------------------------------"
if command -v free &> /dev/null; then
    free -h | grep Mem | awk '{print "  Memory: "$3" used / "$2" total"}'
fi
df -h . | tail -1 | awk '{print "  Disk: "$3" used / "$2" total ("$5" used)"}'

# 7. Recent Activity
echo ""
echo "7️⃣ RECENT ACTIVITY"
echo "-----------------------------------------"
if [ -f "pipeline_test_10k.log" ]; then
    lines=$(wc -l < pipeline_test_10k.log)
    echo "  📄 Pipeline test log: $lines lines"
    echo "  Last 3 log entries:"
    tail -3 pipeline_test_10k.log | sed 's/^/    /'
else
    echo "  ℹ️ No test logs yet"
fi

# Summary
echo ""
echo "========================================="
echo "📊 SUMMARY"
echo "========================================="

# Count checks
checks_passed=0
checks_total=0

# Python check
if command -v python &> /dev/null; then ((checks_passed++)); fi
((checks_total++))

# Docker check  
if command -v docker &> /dev/null && docker info &> /dev/null; then ((checks_passed++)); fi
((checks_total++))

# Config file check
if [ -f "config.yml" ]; then ((checks_passed++)); fi
((checks_total++))

# Docker Compose check
if [ -f "docker-compose.yml" ]; then ((checks_passed++)); fi
((checks_total++))

echo "✅ Checks passed: $checks_passed / $checks_total"

if [ $checks_passed -eq $checks_total ]; then
    echo ""
    echo "🎉 SYSTEM IS READY!"
    echo ""
    echo "To start the pipeline:"
    echo "  docker network create scraping_network"
    echo "  docker-compose up -d"
else
    echo ""
    echo "⚠️ SYSTEM NEEDS ATTENTION"
    echo "Please install missing components above"
fi

echo "========================================="
