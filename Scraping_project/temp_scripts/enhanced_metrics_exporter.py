#!/usr/bin/env python
"""Enhanced Metrics Exporter with Pipeline Statistics

Exposes Prometheus metrics for the entire pipeline:
- Stage 1: URLs discovered, queued
- Stage 2: Pages analyzed, quality metrics
- Stage 3: Summaries created, deduplication stats
- Stage 4: Large docs processed
- Redis metrics
- Real-time pipeline status
"""

from prometheus_client import start_http_server, Gauge, Counter, Histogram, Info
import time
import sys
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.common.storage_manager import get_delta
import redis
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Redis Metrics
redis_keys = Gauge('pipeline_redis_keys', 'Number of keys in Redis')
redis_memory_bytes = Gauge('pipeline_redis_memory_bytes', 'Redis memory usage in bytes')

# Stage 1 Metrics
stage1_urls_discovered = Gauge('stage1_urls_discovered_total', 'Total URLs discovered by Stage 1')
stage1_urls_queued = Gauge('stage1_urls_queued_total', 'URLs queued for Stage 2')

# Stage 2 Metrics
stage2_pages_analyzed = Gauge('stage2_pages_analyzed_total', 'Total pages analyzed')
stage2_quality_docs = Gauge('stage2_quality_docs_total', 'Quality documents found')
stage2_massive_docs = Gauge('stage2_massive_docs_total', 'Massive documents found')
stage2_avg_word_count = Gauge('stage2_avg_word_count', 'Average word count of analyzed pages')
stage2_avg_quality_ratio = Gauge('stage2_avg_text_html_ratio', 'Average text-to-HTML ratio')

# Stage 3 Metrics
stage3_summaries_created = Gauge('stage3_summaries_created_total', 'Total summaries created')
stage3_deduplicated = Gauge('stage3_documents_deduplicated_total', 'Documents removed by deduplication')

# Stage 4 Metrics
stage4_large_summaries = Gauge('stage4_large_doc_summaries_total', 'Large document summaries created')
stage4_avg_compression = Gauge('stage4_avg_compression_ratio', 'Average compression ratio')

# Pipeline Status
pipeline_running = Gauge('pipeline_running', 'Pipeline running status (1=running, 0=stopped)')
pipeline_last_update = Gauge('pipeline_last_update_timestamp', 'Last metrics update timestamp')

# Pipeline Info
pipeline_info = Info('pipeline_info', 'Pipeline version and configuration')

def collect_redis_metrics():
    """Collect Redis metrics."""
    try:
        r = redis.Redis(host='localhost', port=6379, decode_responses=True)
        db_size = r.dbsize()
        info = r.info('memory')

        redis_keys.set(db_size)
        redis_memory_bytes.set(info.get('used_memory', 0))
        return True
    except Exception as e:
        logger.error(f"Error collecting Redis metrics: {e}")
        return False

def collect_delta_metrics():
    """Collect metrics from Delta Lake tables."""
    delta = get_delta()

    try:
        # Stage 1: seed_urls and stage2_queue
        try:
            seeds = delta.read_table('seed_urls')
            stage1_urls_discovered.set(len(seeds))
        except Exception as e:
            logger.debug(f"No seed_urls: {e}")
            stage1_urls_discovered.set(0)

        try:
            queue = delta.read_table('stage2_queue')
            pending = len([item for item in queue if item.get('status') == 'pending'])
            stage1_urls_queued.set(pending)
        except Exception as e:
            logger.debug(f"No stage2_queue: {e}")
            stage1_urls_queued.set(0)

        # Stage 2: stage2_page_analysis
        try:
            analysis = delta.read_table('stage2_page_analysis')
            stage2_pages_analyzed.set(len(analysis))

            quality_docs = [d for d in analysis if not d.get('is_massive_doc', False) and not d.get('is_low_quality', True)]
            massive_docs = [d for d in analysis if d.get('is_massive_doc', False)]

            stage2_quality_docs.set(len(quality_docs))
            stage2_massive_docs.set(len(massive_docs))

            # Calculate averages
            if analysis:
                word_counts = [d.get('word_count', 0) for d in analysis]
                ratios = [d.get('text_to_html_ratio', 0) for d in analysis]

                avg_words = sum(word_counts) / len(word_counts) if word_counts else 0
                avg_ratio = sum(ratios) / len(ratios) if ratios else 0

                stage2_avg_word_count.set(avg_words)
                stage2_avg_quality_ratio.set(avg_ratio)
        except Exception as e:
            logger.debug(f"No stage2_page_analysis: {e}")
            stage2_pages_analyzed.set(0)
            stage2_quality_docs.set(0)
            stage2_massive_docs.set(0)

        # Stage 3: stage3_summaries
        try:
            summaries = delta.read_table('stage4_summaries')  # Stage 3 writes to stage4_summaries
            stage3_summaries_created.set(len(summaries))
        except Exception as e:
            logger.debug(f"No stage3_summaries: {e}")
            stage3_summaries_created.set(0)

        # Stage 4: stage4_large_doc_summaries
        try:
            large_summaries = delta.read_table('stage4_large_doc_summaries')
            stage4_large_summaries.set(len(large_summaries))

            # Calculate average compression
            if large_summaries:
                compressions = [s.get('compression_ratio', 0) for s in large_summaries]
                avg_compression = sum(compressions) / len(compressions) if compressions else 0
                stage4_avg_compression.set(avg_compression)
        except Exception as e:
            logger.debug(f"No stage4_large_doc_summaries: {e}")
            stage4_large_summaries.set(0)

    except Exception as e:
        logger.error(f"Error collecting Delta metrics: {e}")

def main():
    """Main metrics collection loop."""
    port = 9090

    logger.info(f"Starting Enhanced Metrics Exporter on port {port}")
    start_http_server(port)
    logger.info(f"✅ Metrics available at http://localhost:{port}/metrics")

    # Set pipeline info
    pipeline_info.info({
        'version': '1.0.0',
        'stages': '4',
        'storage': 'delta_lake',
        'deduplication': 'redis'
    })

    logger.info("Collecting metrics every 5 seconds...")

    while True:
        try:
            # Collect all metrics
            redis_ok = collect_redis_metrics()
            collect_delta_metrics()

            # Update status
            pipeline_running.set(1 if redis_ok else 0)
            pipeline_last_update.set(time.time())

            logger.info("✅ Metrics updated")

        except Exception as e:
            logger.error(f"Error in metrics collection: {e}")
            pipeline_running.set(0)

        time.sleep(5)

if __name__ == '__main__':
    main()
