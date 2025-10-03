"""
Tests for inter-stage validation system.
Ensures pipeline data integrity and schema compliance.
"""

import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from src.common.interstage_validation import (
    InterstageValidator,
    JSONLValidator,
    validate_pipeline_output,
)
from src.common.schemas_validated import DiscoveryItem, EnrichmentItem, ValidationResult


class TestSchemaValidation:
    """Test Pydantic schema validation"""

    def test_discovery_item_valid(self):
        """Test valid DiscoveryItem passes validation"""
        url = "https://example.com/page"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        data = {
            'source_url': 'https://example.com',
            'discovered_url': url,
            'first_seen': datetime.now().isoformat(),
            'url_hash': url_hash,
            'discovery_depth': 1,
            'discovery_source': 'html_link',
            'confidence': 1.0
        }

        item = DiscoveryItem(**data)
        assert item.discovered_url == url
        assert item.url_hash == url_hash

    def test_discovery_item_hash_mismatch(self):
        """Test that hash mismatch is caught"""
        data = {
            'source_url': 'https://example.com',
            'discovered_url': 'https://example.com/page',
            'first_seen': datetime.now().isoformat(),
            'url_hash': 'a' * 64,
            'discovery_depth': 1
        }

        with pytest.raises(ValueError) as exc_info:
            DiscoveryItem(**data)

        assert 'url_hash mismatch' in str(exc_info.value)

    def test_discovery_item_invalid_url(self):
        """Test that invalid URL format is caught"""
        url = "not_a_valid_url"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        data = {
            'source_url': 'https://example.com',
            'discovered_url': url,
            'first_seen': datetime.now().isoformat(),
            'url_hash': url_hash,
            'discovery_depth': 1
        }

        with pytest.raises(ValueError) as exc_info:
            DiscoveryItem(**data)

        assert 'must start with http://' in str(exc_info.value)

    def test_discovery_item_depth_out_of_range(self):
        """Test that invalid depth is caught"""
        url = "https://example.com/page"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        data = {
            'source_url': 'https://example.com',
            'discovered_url': url,
            'first_seen': datetime.now().isoformat(),
            'url_hash': url_hash,
            'discovery_depth': 15  # Out of range (max 10)
        }

        with pytest.raises(ValueError) as exc_info:
            DiscoveryItem(**data)

        assert 'less than or equal to 10' in str(exc_info.value)

    def test_validation_result_valid(self):
        """Test valid ValidationResult passes validation"""
        url = "https://example.com/page"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        data = {
            'url': url,
            'url_hash': url_hash,
            'status_code': 200,
            'content_type': 'text/html',
            'content_length': 1024,
            'response_time': 0.5,
            'is_valid': True,
            'error_message': None,
            'validated_at': datetime.now().isoformat()
        }

        result = ValidationResult(**data)
        assert result.url == url
        assert result.is_valid is True

    def test_validation_result_requires_error_message(self):
        """Test that is_valid=False requires error_message"""
        url = "https://example.com/page"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        data = {
            'url': url,
            'url_hash': url_hash,
            'status_code': 404,
            'content_type': '',
            'content_length': 0,
            'response_time': 0.1,
            'is_valid': False,
            'error_message': None,  # Missing error message
            'validated_at': datetime.now().isoformat()
        }

        with pytest.raises(ValueError) as exc_info:
            ValidationResult(**data)

        assert 'error_message must be provided' in str(exc_info.value)

    def test_enrichment_item_valid(self):
        """Test valid EnrichmentItem passes validation"""
        url = "https://example.com/page"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        data = {
            'url': url,
            'url_hash': url_hash,
            'title': 'Test Page',
            'text_content': 'This is test content with some words',
            'word_count': 7,
            'entities': ['Test'],
            'keywords': ['test', 'content'],
            'content_tags': ['test'],
            'has_pdf_links': False,
            'has_audio_links': False,
            'status_code': 200,
            'content_type': 'text/html',
            'enriched_at': datetime.now().isoformat()
        }

        item = EnrichmentItem(**data)
        assert item.word_count == 7

    def test_enrichment_item_word_count_mismatch(self):
        """Test that word count mismatch is caught"""
        url = "https://example.com/page"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        data = {
            'url': url,
            'url_hash': url_hash,
            'title': 'Test Page',
            'text_content': 'This is test content',
            'word_count': 100,  # Should be 4
            'entities': [],
            'keywords': [],
            'content_tags': [],
            'has_pdf_links': False,
            'has_audio_links': False,
            'status_code': 200,
            'content_type': 'text/html',
            'enriched_at': datetime.now().isoformat()
        }

        with pytest.raises(ValueError) as exc_info:
            EnrichmentItem(**data)

        assert 'word_count mismatch' in str(exc_info.value)


class TestJSONLValidator:
    """Test JSONL file validation"""

    def create_temp_jsonl(self, records: list) -> Path:
        """Helper to create temporary JSONL file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for record in records:
                f.write(json.dumps(record) + '\n')
            return Path(f.name)

    def test_validate_valid_discovery_file(self):
        """Test validation of valid discovery file"""
        url = "https://example.com/page"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        records = [{
            'source_url': 'https://example.com',
            'discovered_url': url,
            'first_seen': datetime.now().isoformat(),
            'url_hash': url_hash,
            'discovery_depth': 1,
            'discovery_source': 'html_link',
            'confidence': 1.0
        }]

        file_path = self.create_temp_jsonl(records)

        try:
            validator = JSONLValidator('DiscoveryItem')
            report = validator.validate_file(file_path)

            assert report.total_records == 1
            assert report.valid_records == 1
            assert report.invalid_records == 0
            assert report.is_acceptable
        finally:
            file_path.unlink()

    def test_validate_invalid_discovery_file(self):
        """Test validation catches invalid records"""
        records = [{
            'source_url': 'https://example.com',
            'discovered_url': 'https://example.com/page',
            'first_seen': datetime.now().isoformat(),
            # Missing url_hash - required field
            'discovery_depth': 1
        }]

        file_path = self.create_temp_jsonl(records)

        try:
            validator = JSONLValidator('DiscoveryItem')
            report = validator.validate_file(file_path)

            assert report.total_records == 1
            assert report.valid_records == 0
            assert report.invalid_records == 1
            assert len(report.errors) > 0
            assert report.missing_fields_count > 0
        finally:
            file_path.unlink()

    def test_validate_extra_fields_rejected(self):
        """Test that extra fields are rejected"""
        url = "https://example.com/page"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        records = [{
            'source_url': 'https://example.com',
            'discovered_url': url,
            'first_seen': datetime.now().isoformat(),
            'url_hash': url_hash,
            'discovery_depth': 1,
            'extra_field': 'should_not_be_here'  # Extra field
        }]

        file_path = self.create_temp_jsonl(records)

        try:
            validator = JSONLValidator('DiscoveryItem')
            report = validator.validate_file(file_path)

            assert report.invalid_records > 0
            assert report.extra_fields_count > 0
        finally:
            file_path.unlink()


class TestInterstageValidation:
    """Test inter-stage data integrity validation"""

    def create_discovery_file(self, urls: list) -> Path:
        """Helper to create discovery output file"""
        records = []
        for url in urls:
            url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()
            records.append({
                'source_url': 'https://example.com',
                'discovered_url': url,
                'first_seen': datetime.now().isoformat(),
                'url_hash': url_hash,
                'discovery_depth': 1,
                'discovery_source': 'html_link',
                'confidence': 1.0
            })

        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for record in records:
                f.write(json.dumps(record) + '\n')
            return Path(f.name)

    def create_validation_file(self, url_hashes: list) -> Path:
        """Helper to create validation output file"""
        records = []
        for url_hash in url_hashes:
            records.append({
                'url': f'https://example.com/page-{url_hash[:8]}',
                'url_hash': url_hash,
                'status_code': 200,
                'content_type': 'text/html',
                'content_length': 1024,
                'response_time': 0.5,
                'is_valid': True,
                'error_message': None,
                'validated_at': datetime.now().isoformat()
            })

        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for record in records:
                f.write(json.dumps(record) + '\n')
            return Path(f.name)

    def test_stage1_to_stage2_perfect_coverage(self):
        """Test validation with perfect coverage"""
        urls = [
            'https://example.com/page1',
            'https://example.com/page2',
            'https://example.com/page3'
        ]

        stage1_file = self.create_discovery_file(urls)
        url_hashes = [hashlib.sha256(url.encode('utf-8')).hexdigest() for url in urls]
        stage2_file = self.create_validation_file(url_hashes)

        try:
            validator = InterstageValidator(fail_on_error=False)
            report, stats = validator.validate_stage1_to_stage2(stage1_file, stage2_file)

            assert stats['stage1_total'] == 3
            assert stats['stage2_total'] == 3
            assert stats['missing_in_stage2'] == 0
            assert stats['extra_in_stage2'] == 0
            assert stats['coverage_percent'] == 100.0
        finally:
            stage1_file.unlink()
            stage2_file.unlink()

    def test_stage1_to_stage2_orphaned_hashes(self):
        """Test detection of orphaned hashes in Stage 2"""
        urls = [
            'https://example.com/page1',
            'https://example.com/page2'
        ]

        stage1_file = self.create_discovery_file(urls)
        url_hashes = [hashlib.sha256(url.encode('utf-8')).hexdigest() for url in urls]

        # Add extra hash not from Stage 1
        orphaned_hash = 'a' * 64  # Invalid hash not from Stage 1
        url_hashes.append(orphaned_hash)

        stage2_file = self.create_validation_file(url_hashes)

        try:
            validator = InterstageValidator(fail_on_error=False)
            report, stats = validator.validate_stage1_to_stage2(stage1_file, stage2_file)

            assert stats['extra_in_stage2'] == 1
            assert len(report.errors) > 0
            assert 'orphaned' in report.errors[0]['type']
        finally:
            stage1_file.unlink()
            stage2_file.unlink()

    def test_stage1_to_stage2_missing_urls(self):
        """Test detection of URLs missing in Stage 2"""
        urls = [
            'https://example.com/page1',
            'https://example.com/page2',
            'https://example.com/page3'
        ]

        stage1_file = self.create_discovery_file(urls)

        # Only validate 2 of 3 URLs
        url_hashes = [
            hashlib.sha256(urls[0].encode('utf-8')).hexdigest(),
            hashlib.sha256(urls[1].encode('utf-8')).hexdigest()
        ]
        stage2_file = self.create_validation_file(url_hashes)

        try:
            validator = InterstageValidator(fail_on_error=False)
            report, stats = validator.validate_stage1_to_stage2(stage1_file, stage2_file)

            assert stats['missing_in_stage2'] == 1
            assert stats['coverage_percent'] < 100.0
            assert len(report.warnings) > 0
        finally:
            stage1_file.unlink()
            stage2_file.unlink()


class TestValidationCLI:
    """Test validation CLI tool integration"""

    def test_validate_pipeline_output_stage1_only(self):
        """Test validation of Stage 1 only"""
        url = "https://example.com/page"
        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()

        records = [{
            'source_url': 'https://example.com',
            'discovered_url': url,
            'first_seen': datetime.now().isoformat(),
            'url_hash': url_hash,
            'discovery_depth': 1,
            'discovery_source': 'html_link',
            'confidence': 1.0
        }]

        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            for record in records:
                f.write(json.dumps(record) + '\n')
            stage1_file = Path(f.name)

        try:
            results = validate_pipeline_output(stage1_file, fail_on_error=False)

            assert 'stage1_schema' in results
            assert results['stage1_schema'].is_acceptable
        finally:
            stage1_file.unlink()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])