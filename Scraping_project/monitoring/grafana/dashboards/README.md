# Scraping Pipeline Grafana Dashboards

## Overview

This directory contains Grafana dashboard JSON files for monitoring the UConn scraping pipeline.

## Dashboard: Scraping Pipeline Health

**File:** `scraping_pipeline_health.json`

A comprehensive unified dashboard for monitoring all aspects of the scraping pipeline with drill-down capabilities.

### Features

#### Variables (Filters)
- **Datasource**: Prometheus datasource selector
- **Environment** (`$env`): Filter by environment (default: "local")
- **Spider** (`$spider`): Filter by spider name (scout, depth, js_spider, etc.) - multi-select
- **Stage** (`$stage`): Filter by processing stage (stage1, stage2, stage3, stage4) - multi-select

All variables support regex patterns for flexible filtering.

### Dashboard Rows

#### 1. Overview - Pipeline Health
High-level metrics for quick health assessment:
- **Total URLs Discovered**: Overall discovery count
- **Total UConn URLs**: UConn-specific URLs found
- **Seed URLs Available**: Active seed URL count
- **Delta Lake Total Records**: All records across tables
- **Circuit Breakers Open**: Rate limiting status
- **Active Spiders**: Currently running spiders
- **URLs Processed per Second by Stage**: Throughput chart
- **Average Response Time by Spider**: Performance latency

#### 2. Stage 1 - Discovery
Spider-specific discovery metrics:
- **Scout Spider Activity**: Items scraped/sec for scout spider
- **Depth Spider Activity**: Items scraped/sec for depth spider
- **JS Spider Activity**: Items scraped/sec for JavaScript-heavy pages
- **New URL Discovery Rate**: Stacked chart of all spiders
- **Stage 2 Queue Lengths**: Downstream queue depths

#### 3. Error Tracking
Comprehensive error monitoring:
- **Errors by Type and Stage**: Stacked error rates across all stages
- **Spider Errors by Exception Type**: Exception breakdown per spider
- **HTTP Response Codes Distribution**: Percentage distribution of status codes

#### 4. Storage & Infrastructure
System health and resource monitoring:
- **Delta Lake Table Sizes**: Storage size in bytes per table
- **Delta Lake Record Counts**: Number of records per table
- **Service Health Status**: Binary up/down status for Postgres, Redis, and Metrics Exporter
- **Redis Queue Depths**: All queue lengths
- **Network Throughput**: Bytes sent/received per spider

### Metrics Used

#### From metrics_exporter.py
- `total_urls_discovered`
- `total_uconn_urls`
- `total_seed_urls`
- `urls_processed_per_second{stage}`
- `delta_lake_total_records`
- `delta_lake_records{table}`
- `delta_lake_size_bytes{table}`
- `circuit_breaker_open_count`
- `redis_queue_length{queue}`
- `errors_total{stage,error_type}`

#### From scrapy_prometheus.py
- `scrapy_spider_opened{spider}`
- `scrapy_items_scraped_total{spider}`
- `scrapy_response_time_seconds{spider}`
- `scrapy_responses_total{spider,status_code}`
- `scrapy_spider_errors_total{spider,exception_type}`
- `scrapy_downloader_request_bytes_total{spider}`
- `scrapy_downloader_response_bytes_total{spider}`

#### Infrastructure
- `up{job}` - Service health status from Prometheus

## Installation & Provisioning

### Option 1: Docker Compose (Automatic)

The dashboard is automatically provisioned when using the monitoring stack:

```bash
docker-compose -f docker-compose.monitoring.yml up -d
```

The dashboard will be available at: `http://localhost:3000/d/scraping-pipeline-health`

### Option 2: Manual Import

1. Open Grafana UI (default: http://localhost:3000)
2. Navigate to **Dashboards** → **Import**
3. Upload `scraping_pipeline_health.json`
4. Select the Prometheus datasource
5. Click **Import**

### Option 3: Grafana Provisioning Directory

Copy the dashboard to your Grafana provisioning directory:

```bash
# For Docker
cp scraping_pipeline_health.json /etc/grafana/provisioning/dashboards/grafana/

# For local Grafana installation
cp scraping_pipeline_health.json /usr/share/grafana/conf/provisioning/dashboards/
```

Restart Grafana to load the dashboard:

```bash
# Docker
docker-compose restart grafana

# Systemd
sudo systemctl restart grafana-server
```

## Configuration Files

- **Provisioning**: `/monitoring/grafana_dashboards.yml`
- **Dashboard**: `/monitoring/grafana/dashboards/scraping_pipeline_health.json`

## Customization

### Adding New Panels

Panels use the Grafana 10.x schema. To add a new panel:

1. Edit the JSON file
2. Add a new panel object to the `panels` array
3. Set `gridPos` for positioning:
   - `x`: Horizontal position (0-23)
   - `y`: Vertical position (increases downward)
   - `w`: Width (1-24, where 24 is full width)
   - `h`: Height (in grid units)

### Updating Variables

Variables are defined in the `templating.list` array. Each variable supports:
- **query**: Prometheus query for options (e.g., `label_values(metric, label)`)
- **regex**: Filter results with regex
- **multi**: Allow multiple selections
- **includeAll**: Add "All" option
- **allValue**: Value to use when "All" is selected (use `.*` for regex match)

### Panel Types

Common panel types used:
- `stat`: Single value with sparkline
- `timeseries`: Time-series graph
- `table`: Tabular data display
- `row`: Collapsible section header

## Troubleshooting

### Dashboard Not Loading

1. Verify Prometheus is scraping metrics:
   ```bash
   curl http://localhost:9090/api/v1/query?query=up
   ```

2. Check Grafana datasource configuration:
   - Navigate to **Configuration** → **Data Sources**
   - Verify Prometheus URL: `http://prometheus:9090`

3. Verify dashboard JSON is valid:
   ```bash
   python3 -m json.tool scraping_pipeline_health.json > /dev/null
   ```

### No Data in Panels

1. Ensure the metrics exporter is running:
   ```bash
   curl http://localhost:9090/metrics | grep total_urls_discovered
   ```

2. Check that spiders are active:
   ```bash
   curl http://localhost:9090/metrics | grep scrapy_spider_opened
   ```

3. Verify time range is correct (default: Last 1 hour)

### Variables Not Populating

1. Check that the source metrics exist:
   ```promql
   scrapy_spider_opened  # Should return results with 'spider' label
   urls_processed_per_second  # Should return results with 'stage' label
   ```

2. Verify regex patterns in variable definitions

## Refresh Rate

- **Default**: 10 seconds
- **Configurable intervals**: 5s, 10s, 30s, 1m, 5m, 15m, 30m, 1h, 2h

Change via the refresh dropdown in the Grafana UI (top right).

## Version

- **Dashboard Version**: 1.0
- **Grafana Schema Version**: 38
- **Compatible with**: Grafana 10.x+

## Tags

- `scraping`
- `pipeline`
- `uconn`

Use these tags to search for the dashboard in Grafana.
