# Kafka Real-Time Metrics Setup Guide

## Overview

This guide explains how Kafka sends real-time data to Prometheus for monitoring the scraping pipeline.

## Architecture Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     Real-Time Metrics Flow                       │
└─────────────────────────────────────────────────────────────────┘

Scrapy App ──┐
             │
             ▼
         Kafka Broker ─────────┐
             │                 │
             │                 │ [JMX Port 9999]
             │                 │
             ▼                 ▼
    Kafka Consumer      JMX Exporter:5556
    (kafka-delta-       (bitnami/jmx-exporter)
     ingest)                   │
         │                     │
         │ [UDP:9125]          │ [HTTP /metrics]
         │                     │
         ▼                     │
    StatsD Exporter:9102       │
         │                     │
         │ [HTTP /metrics]     │
         │                     │
         └─────────┬───────────┘
                   │
                   ▼
           Prometheus:9091
           (scrape_interval: 10s)
                   │
                   ▼
             Grafana:3000
           (Real-time dashboards)
```

## Components

### 1. Kafka Broker with JMX Enabled

**Configuration** (in [docker-compose.yml](docker-compose.yml)):
```yaml
kafka:
  environment:
    # JMX configuration for metrics export
    KAFKA_JMX_PORT: 9999
    KAFKA_JMX_HOSTNAME: kafka
    KAFKA_JMX_OPTS: >-
      -Dcom.sun.management.jmxremote
      -Dcom.sun.management.jmxremote.authenticate=false
      -Dcom.sun.management.jmxremote.ssl=false
      -Dcom.sun.management.jmxremote.local.only=false
      -Dcom.sun.management.jmxremote.rmi.port=9999
  ports:
    - "9999:9999"  # JMX metrics port
```

**What it does:**
- Exposes internal Kafka metrics via JMX protocol on port 9999
- Provides real-time broker, topic, and partition metrics

### 2. JMX Exporter

**Configuration** (in [docker-compose.yml](docker-compose.yml)):
```yaml
kafka-jmx-exporter:
  image: bitnami/jmx-exporter:latest
  ports:
    - "5556:5556"
  environment:
    - JMX_HOST=kafka
    - JMX_PORT=9999
  volumes:
    - ./monitoring/kafka_jmx_config.yml:/etc/jmx-exporter/config.yml:ro
```

**What it does:**
- Connects to Kafka's JMX port (9999)
- Converts JMX metrics to Prometheus format
- Exposes metrics via HTTP at `http://localhost:5556/metrics`

**Key Metrics Exported** (from [kafka_jmx_config.yml](monitoring/kafka_jmx_config.yml)):
- `kafka_server_brokertopicmetrics_messagesin_total` - Total messages received per topic
- `kafka_server_brokertopicmetrics_bytesin_total` - Total bytes received per topic
- `kafka_server_brokertopicmetrics_bytesout_total` - Total bytes sent per topic
- `kafka_consumer_records_lag` - Consumer lag (messages behind)
- `kafka_controller_activecontrollercount` - Active controllers (should be 1)
- `kafka_server_replicamanager_underreplicatedpartitions` - Under-replicated partitions

### 3. StatsD Exporter (for kafka-delta-ingest)

**Configuration** (in [docker-compose.yml](docker-compose.yml)):
```yaml
statsd-exporter:
  image: prom/statsd-exporter:latest
  ports:
    - "9102:9102"  # Prometheus metrics endpoint
    - "9125:9125/udp"  # StatsD UDP receiver
  volumes:
    - ./monitoring/statsd_mapping.yml:/etc/statsd/statsd_mapping.yml:ro
```

**What it does:**
- Receives StatsD metrics from kafka-delta-ingest daemon via UDP
- Converts StatsD metrics to Prometheus format
- Exposes metrics via HTTP at `http://localhost:9102/metrics`

**Metrics from kafka-delta-ingest:**
- `kafka_delta_ingest_messages_received` - Messages consumed from Kafka
- `kafka_delta_ingest_records_written` - Records written to Delta Lake
- `kafka_delta_ingest_batches_written` - Batches committed to Delta Lake
- `kafka_delta_ingest_errors_*` - Various error counters

### 4. Prometheus

**Configuration** (in [monitoring/prometheus.yml](monitoring/prometheus.yml)):
```yaml
scrape_configs:
  # Kafka JMX metrics - scraped every 10 seconds
  - job_name: 'kafka_jmx'
    scrape_interval: 10s
    scrape_timeout: 5s
    static_configs:
      - targets: ['kafka-jmx-exporter:5556']

  # StatsD metrics - scraped every 10 seconds
  - job_name: 'statsd'
    scrape_interval: 10s
    scrape_timeout: 5s
    static_configs:
      - targets: ['statsd-exporter:9102']
```

**What it does:**
- Scrapes metrics from JMX Exporter every 10 seconds
- Scrapes metrics from StatsD Exporter every 10 seconds
- Stores time-series data for querying and alerting
- Provides query API for Grafana

### 5. Grafana

**Configuration:**
- Pre-configured Prometheus datasource
- Real-time dashboard with 5-second refresh
- Custom Kafka metrics dashboard

**Access:**
- URL: http://localhost:3000
- Credentials: admin / admin

## Verification Steps

### 1. Start the Complete Stack

```bash
./scripts/startup-pipeline.sh
```

### 2. Verify Kafka Metrics Flow

```bash
./scripts/verify-kafka-metrics.sh
```

This script will:
1. ✅ Verify Kafka JMX is enabled and accessible
2. ✅ Check JMX Exporter is running and connected
3. ✅ Verify Prometheus is scraping metrics
4. ✅ Send test messages to Kafka
5. ✅ Confirm metrics update in real-time
6. ✅ Query key Kafka metrics

### 3. Manual Verification

#### Check JMX Exporter Metrics
```bash
curl http://localhost:5556/metrics | grep kafka_server_brokertopicmetrics
```

Expected output:
```
kafka_server_brokertopicmetrics_messagesin_total{topic="scraped-items"} 1234
kafka_server_brokertopicmetrics_bytesin_total{topic="scraped-items"} 567890
```

#### Check StatsD Exporter Metrics
```bash
curl http://localhost:9102/metrics | grep kafka_delta_ingest
```

Expected output:
```
kafka_delta_ingest_messages_received 1234
kafka_delta_ingest_records_written 1234
kafka_delta_ingest_batches_written 12
```

#### Check Prometheus Targets
```bash
curl http://localhost:9091/api/v1/targets | jq '.data.activeTargets[] | select(.labels.job=="kafka_jmx") | {health, lastScrape}'
```

Expected output:
```json
{
  "health": "up",
  "lastScrape": "2024-01-01T12:34:56.789Z"
}
```

#### Query Kafka Metrics from Prometheus
```bash
# Message rate (messages per second)
curl -s 'http://localhost:9091/api/v1/query?query=rate(kafka_server_brokertopicmetrics_messagesin_total{topic="scraped-items"}[1m])' | jq '.data.result[0].value'

# Consumer lag
curl -s 'http://localhost:9091/api/v1/query?query=kafka_consumer_records_lag' | jq '.data.result[0].value'
```

## Real-Time Monitoring in Grafana

### 1. Import Kafka Dashboard

1. Access Grafana: http://localhost:3000
2. Login: admin / admin
3. Navigate to: Dashboards → Import
4. Upload file: `monitoring/grafana_kafka_realtime_dashboard.json`

### 2. Key Panels

The dashboard includes:

- **Messages In Rate**: Real-time message ingestion rate (updates every 5 seconds)
- **Bytes In Rate**: Data throughput in bytes/sec
- **Consumer Lag**: How far behind the consumer is (critical for monitoring backlog)
- **Total Messages**: Cumulative message count
- **Active Controller**: Should always be 1
- **Under-Replicated Partitions**: Should always be 0
- **Failed Produce Requests**: Error monitoring
- **kafka-delta-ingest Metrics**: Ingest pipeline health

### 3. Set Dashboard Refresh

- Click dashboard settings (gear icon)
- Set auto-refresh: 5s or 10s
- Save dashboard

## Alerting

### Critical Alerts to Configure

1. **High Consumer Lag**
```promql
kafka_consumer_records_lag > 10000
```

2. **Kafka Broker Down**
```promql
up{job="kafka_jmx"} == 0
```

3. **High Error Rate**
```promql
rate(kafka_delta_ingest_errors_total[5m]) > 10
```

4. **Under-Replicated Partitions**
```promql
kafka_server_replicamanager_underreplicatedpartitions > 0
```

Configure these in [monitoring/alerting_rules.yml](monitoring/alerting_rules.yml).

## Troubleshooting

### Issue: No Kafka metrics in Prometheus

**Solution:**
1. Verify JMX is enabled:
```bash
docker-compose exec kafka env | grep JMX
```

2. Check JMX Exporter logs:
```bash
docker-compose logs kafka-jmx-exporter
```

3. Test JMX connectivity:
```bash
docker-compose exec kafka-jmx-exporter nc -zv kafka 9999
```

4. Verify exporter config:
```bash
curl http://localhost:5556/metrics | head -20
```

### Issue: Metrics delayed or not updating

**Solution:**
1. Check Prometheus scrape interval (should be 10s)
2. Verify Prometheus is reaching the target:
```bash
curl http://localhost:9091/api/v1/targets
```

3. Send test messages to force metric update:
```bash
./scripts/test-pipeline.sh
```

### Issue: Consumer lag metrics missing

**Solution:**
Consumer lag metrics only appear when there's an active consumer. Ensure kafka-delta-ingestor is running:
```bash
docker-compose ps kafka-delta-ingestor
docker-compose logs kafka-delta-ingestor
```

## Performance Tuning

### Reduce Scrape Interval for More Real-Time Data

In [monitoring/prometheus.yml](monitoring/prometheus.yml):
```yaml
scrape_configs:
  - job_name: 'kafka_jmx'
    scrape_interval: 5s  # From 10s to 5s
```

⚠️ **Warning:** Lower intervals increase Prometheus resource usage.

### Increase Metric Retention

In [docker-compose.yml](docker-compose.yml):
```yaml
prometheus-a:
  command:
    - '--storage.tsdb.retention.time=90d'  # From 30d to 90d
```

## Example PromQL Queries

### Message Throughput
```promql
# Messages per second (last 1 minute average)
rate(kafka_server_brokertopicmetrics_messagesin_total{topic="scraped-items"}[1m])

# Bytes per second
rate(kafka_server_brokertopicmetrics_bytesin_total{topic="scraped-items"}[1m])
```

### Latency Monitoring
```promql
# Request latency 95th percentile
histogram_quantile(0.95, rate(kafka_network_requestmetrics_totaltimems_bucket[5m]))
```

### Consumer Health
```promql
# Consumer lag
kafka_consumer_records_lag

# Ingest rate
rate(kafka_delta_ingest_messages_received[1m])
```

### Error Monitoring
```promql
# Failed produce requests
rate(kafka_server_brokertopicmetrics_failedproducerequests_total[5m])

# Delta Lake write errors
rate(kafka_delta_ingest_errors_write_failed[5m])
```

## Summary

✅ Kafka JMX metrics exposed on port 9999
✅ JMX Exporter converts to Prometheus format (port 5556)
✅ StatsD Exporter receives kafka-delta-ingest metrics (port 9102)
✅ Prometheus scrapes both every 10 seconds
✅ Grafana displays real-time dashboards with 5-second refresh
✅ Complete observability of Kafka → Delta Lake pipeline

**Result:** Real-time visibility into message flow, throughput, lag, and errors across the entire pipeline.
