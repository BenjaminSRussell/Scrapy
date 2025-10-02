import json
from pathlib import Path

import pytest

from src.common import config_keys as keys
from src.orchestrator.pipeline import PipelineOrchestrator


class DummyConfig:
    def __init__(self, stage2_path: Path, stage3_path: Path, temp_dir: Path):
        self._stage2_path = Path(stage2_path)
        self._stage3_path = Path(stage3_path)
        self._temp_dir = Path(temp_dir)

    def get_stage2_config(self):
        return {
            keys.VALIDATION_MAX_WORKERS: 4,
            keys.VALIDATION_TIMEOUT: 5,
            keys.VALIDATION_OUTPUT_FILE: str(self._stage2_path),
        }

    def get_stage3_config(self):
        return {
            keys.ENRICHMENT_BATCH_SIZE: 5,
            keys.ENRICHMENT_OUTPUT_FILE: str(self._stage3_path),
            keys.ENRICHMENT_ALLOWED_DOMAINS: ['example.com'],
            keys.ENRICHMENT_CONTENT_TYPES: {},
            keys.ENRICHMENT_HEADLESS_BROWSER: {},
            keys.ENRICHMENT_NLP_ENABLED: False,
        }

    def get_data_paths(self):
        return {keys.TEMP_DIR: self._temp_dir}


class FakeCrawlerProcess:
    def __init__(self, settings):
        self.settings = settings
        self.crawled_spider_cls = None
        self.crawled_kwargs = None

    def crawl(self, spider_cls, **kwargs):
        self.crawled_spider_cls = spider_cls
        self.crawled_kwargs = kwargs

    def start(self):
        output_file = Path(self.settings['STAGE3_OUTPUT_FILE'])
        output_file.parent.mkdir(parents=True, exist_ok=True)

        metadata_lookup = {
            entry['url']: entry for entry in self.crawled_kwargs['validation_metadata']
        }

        with output_file.open('w', encoding='utf-8') as handle:
            for url in self.crawled_kwargs['urls_list']:
                entry = metadata_lookup[url]
                payload = {
                    'url': url,
                    'url_hash': entry['url_hash'],
                    'status_code': entry['status_code'],
                    'processed_at': 'fake-run',
                }
                handle.write(json.dumps(payload) + '\n')

    def stop(self):
        return None


class DummySpider:
    name = 'dummy-enricher'


@pytest.mark.asyncio
async def test_stage3_enrichment_end_to_end(tmp_path):
    stage2_file = tmp_path / 'stage02.jsonl'
    stage3_file = tmp_path / 'stage03.jsonl'
    temp_dir = tmp_path / 'temp'
    temp_dir.mkdir()

    records = [
        {
            'url': 'https://example.com/valid-1',
            'url_hash': 'hash-valid-1',
            'status_code': 200,
            'content_type': 'text/html',
            'validated_at': '2025-01-01T00:00:00',
            'is_valid': True,
        },
        {
            'url': 'https://example.com/skip-me',
            'url_hash': 'hash-invalid',
            'status_code': 404,
            'content_type': 'text/html',
            'validated_at': '2025-01-01T00:05:00',
            'is_valid': False,
        },
        {
            'url': 'https://example.com/valid-2',
            'url_hash': 'hash-valid-2',
            'status_code': 200,
            'content_type': 'text/html',
            'validated_at': '2025-01-01T00:10:00',
            'is_valid': True,
        },
    ]

    with stage2_file.open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record) + '\n')

    config = DummyConfig(stage2_file, stage3_file, temp_dir)
    orchestrator = PipelineOrchestrator(config)

    created_processes = []

    def process_factory(settings):
        process = FakeCrawlerProcess(settings)
        created_processes.append(process)
        return process

    scrapy_settings = {'STAGE3_OUTPUT_FILE': str(stage3_file)}

    await orchestrator.run_concurrent_stage3_enrichment(
        spider_cls=DummySpider,
        scrapy_settings=scrapy_settings,
        spider_kwargs={'extra': 'value'},
        crawler_process_factory=process_factory,
    )

    assert stage3_file.exists()
    output_lines = [line for line in stage3_file.read_text(encoding='utf-8').splitlines() if line.strip()]
    assert len(output_lines) == 2

    payloads = [json.loads(line) for line in output_lines]
    assert {entry['url'] for entry in payloads} == {
        'https://example.com/valid-1',
        'https://example.com/valid-2',
    }

    process = created_processes[0]
    assert process.crawled_spider_cls is DummySpider
    assert process.crawled_kwargs['urls_list'] == [
        'https://example.com/valid-1',
        'https://example.com/valid-2',
    ]
    assert process.crawled_kwargs['extra'] == 'value'
    assert len(process.crawled_kwargs['validation_metadata']) == 2