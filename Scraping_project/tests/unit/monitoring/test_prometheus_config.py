"""Validate Prometheus scrape configuration for dashboard coverage."""

from pathlib import Path

import yaml


def load_prom_config():
    return yaml.safe_load(Path('monitoring/prometheus.yml').read_text())


def test_alertmanager_targets_complete():
    config = load_prom_config()
    targets = config['alerting']['alertmanagers'][0]['static_configs'][0]['targets']
    assert {'alertmanager-1:9093', 'alertmanager-2:9093', 'alertmanager-3:9093'} <= set(targets)


def test_scrape_jobs_cover_core_services():
    config = load_prom_config()
    jobs = {job['job_name']: job for job in config['scrape_configs']}
    expected_jobs = {
        'scrapy_app',
        'kafka_ingestor',
        'scraping_pipeline',
        'redis',
        'postgres',
        'kafka_jmx',
        'statsd',
    }
    assert expected_jobs <= jobs.keys()
    assert jobs['scrapy_app']['static_configs'][0]['targets']
    assert jobs['scraping_pipeline']['static_configs'][0]['targets'] == ['metrics-exporter:9090']
