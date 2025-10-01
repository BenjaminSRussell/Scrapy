"""
Inter-stage validation to ensure pipeline data integrity.
Validates that Stage N output meets requirements of Stage N+1.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Set, Optional, Tuple, Any
from dataclasses import dataclass
from collections import defaultdict
from pydantic import ValidationError

from src.common.schemas_validated import (
    DiscoveryItem,
    ValidationResult,
    EnrichmentItem,
    SchemaRegistry
)

logger = logging.getLogger(__name__)


class ValidationFailure(Exception):
    """Raised when inter-stage validation fails critically"""
    pass


@dataclass
class ValidationReport:
    """Report of validation results"""
    stage: str
    total_records: int
    valid_records: int
    invalid_records: int
    errors: List[Dict[str, Any]]
    warnings: List[str]
    missing_fields_count: int
    extra_fields_count: int
    type_errors_count: int
    value_errors_count: int

    @property
    def success_rate(self) -> float:
        """Calculate validation success rate"""
        if self.total_records == 0:
            return 0.0
        return (self.valid_records / self.total_records) * 100

    @property
    def is_acceptable(self) -> bool:
        """Check if validation rate is acceptable (>95%)"""
        return self.success_rate >= 95.0

    def summary(self) -> str:
        """Get human-readable summary"""
        lines = [
            f"\n{'='*60}",
            f"Inter-Stage Validation Report: {self.stage}",
            f"{'='*60}",
            f"Total Records: {self.total_records:,}",
            f"Valid Records: {self.valid_records:,} ({self.success_rate:.2f}%)",
            f"Invalid Records: {self.invalid_records:,}",
            "",
            f"Error Breakdown:",
            f"  - Missing Fields: {self.missing_fields_count}",
            f"  - Extra Fields: {self.extra_fields_count}",
            f"  - Type Errors: {self.type_errors_count}",
            f"  - Value Errors: {self.value_errors_count}",
            ""
        ]

        if self.warnings:
            lines.append(f"Warnings ({len(self.warnings)}):")
            for warning in self.warnings[:5]:  # Show first 5
                lines.append(f"  - {warning}")
            if len(self.warnings) > 5:
                lines.append(f"  ... and {len(self.warnings) - 5} more")
            lines.append("")

        if self.errors:
            lines.append(f"Sample Errors ({min(5, len(self.errors))} of {len(self.errors)}):")
            for error in self.errors[:5]:
                lines.append(f"  Line {error.get('line_number', 'N/A')}: {error.get('error', 'Unknown error')}")
            if len(self.errors) > 5:
                lines.append(f"  ... and {len(self.errors) - 5} more errors")
            lines.append("")

        status = "✅ PASS" if self.is_acceptable else "❌ FAIL"
        lines.append(f"Status: {status}")
        lines.append(f"{'='*60}\n")

        return "\n".join(lines)


class JSONLValidator:
    """Validates JSONL files against Pydantic schemas"""

    def __init__(self, schema_name: str, fail_on_error: bool = False, sample_rate: float = 1.0):
        """
        Initialize validator

        Args:
            schema_name: Name of schema to validate against
            fail_on_error: Whether to raise exception on validation failure
            sample_rate: Fraction of records to validate (0.0-1.0), 1.0 = all
        """
        self.schema_name = schema_name
        self.fail_on_error = fail_on_error
        self.sample_rate = sample_rate
        self.model = SchemaRegistry.get_model(schema_name)

        if not self.model:
            raise ValueError(f"Unknown schema: {schema_name}")

    def validate_file(self, file_path: Path) -> ValidationReport:
        """
        Validate all records in JSONL file

        Args:
            file_path: Path to JSONL file

        Returns:
            ValidationReport with results

        Raises:
            ValidationFailure: If validation fails critically and fail_on_error=True
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        total_records = 0
        valid_records = 0
        errors = []
        warnings = []

        missing_fields_count = 0
        extra_fields_count = 0
        type_errors_count = 0
        value_errors_count = 0

        logger.info(f"Validating {file_path} against {self.schema_name} schema...")

        with open(file_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                total_records += 1

                # Sample only a portion if sample_rate < 1.0
                if self.sample_rate < 1.0:
                    import random
                    if random.random() > self.sample_rate:
                        valid_records += 1  # Assume valid for non-sampled
                        continue

                try:
                    data = json.loads(line.strip())
                except json.JSONDecodeError as e:
                    errors.append({
                        'line_number': line_num,
                        'error': f"JSON decode error: {e}",
                        'type': 'json_error'
                    })
                    continue

                # Validate against schema
                try:
                    validated = self.model(**data)
                    valid_records += 1
                except ValidationError as e:
                    # Categorize errors
                    for error in e.errors():
                        error_type = error['type']
                        if 'missing' in error_type:
                            missing_fields_count += 1
                        elif 'extra' in error_type:
                            extra_fields_count += 1
                        elif 'type_error' in error_type:
                            type_errors_count += 1
                        else:
                            value_errors_count += 1

                    errors.append({
                        'line_number': line_num,
                        'error': str(e),
                        'type': 'validation_error',
                        'data_sample': str(data)[:100]
                    })
                except Exception as e:
                    errors.append({
                        'line_number': line_num,
                        'error': f"Unexpected error: {e}",
                        'type': 'unexpected_error'
                    })

        report = ValidationReport(
            stage=self.schema_name,
            total_records=total_records,
            valid_records=valid_records,
            invalid_records=total_records - valid_records,
            errors=errors,
            warnings=warnings,
            missing_fields_count=missing_fields_count,
            extra_fields_count=extra_fields_count,
            type_errors_count=type_errors_count,
            value_errors_count=value_errors_count
        )

        logger.info(report.summary())

        if self.fail_on_error and not report.is_acceptable:
            raise ValidationFailure(
                f"Validation failed for {file_path}: {report.invalid_records} invalid records "
                f"({100 - report.success_rate:.2f}% failure rate)"
            )

        return report


class InterstageValidator:
    """Validates data consistency between pipeline stages"""

    def __init__(self, fail_on_error: bool = False):
        """
        Initialize inter-stage validator

        Args:
            fail_on_error: Whether to raise exception on validation failure
        """
        self.fail_on_error = fail_on_error

    def validate_stage1_to_stage2(
        self,
        stage1_file: Path,
        stage2_file: Path
    ) -> Tuple[ValidationReport, Dict[str, Any]]:
        """
        Validate Stage 1 → Stage 2 data integrity

        Checks:
        - All Stage 1 url_hashes appear in Stage 2
        - Stage 2 only contains url_hashes from Stage 1
        - Timestamps are consistent

        Args:
            stage1_file: Stage 1 discovery output
            stage2_file: Stage 2 validation output

        Returns:
            Tuple of (ValidationReport, integrity_stats)
        """
        logger.info("Validating Stage 1 → Stage 2 integrity...")

        # Load Stage 1 url_hashes
        stage1_hashes = set()
        stage1_urls = {}  # hash -> url mapping

        with open(stage1_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    url_hash = data.get('url_hash')
                    discovered_url = data.get('discovered_url')
                    if url_hash:
                        stage1_hashes.add(url_hash)
                        stage1_urls[url_hash] = discovered_url
                except json.JSONDecodeError:
                    continue

        # Load Stage 2 url_hashes
        stage2_hashes = set()
        stage2_urls = {}  # hash -> url mapping

        with open(stage2_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    url_hash = data.get('url_hash')
                    url = data.get('url')
                    if url_hash:
                        stage2_hashes.add(url_hash)
                        stage2_urls[url_hash] = url
                except json.JSONDecodeError:
                    continue

        # Check integrity
        missing_in_stage2 = stage1_hashes - stage2_hashes
        extra_in_stage2 = stage2_hashes - stage1_hashes

        warnings = []
        errors = []

        if missing_in_stage2:
            warnings.append(
                f"{len(missing_in_stage2)} URL hashes from Stage 1 not found in Stage 2 "
                f"(may be expected if validation filtered some)"
            )

        if extra_in_stage2:
            errors.append({
                'error': f"{len(extra_in_stage2)} URL hashes in Stage 2 not found in Stage 1",
                'type': 'orphaned_hashes',
                'sample': list(extra_in_stage2)[:5]
            })

        coverage = len(stage2_hashes) / len(stage1_hashes) * 100 if stage1_hashes else 0

        integrity_stats = {
            'stage1_total': len(stage1_hashes),
            'stage2_total': len(stage2_hashes),
            'missing_in_stage2': len(missing_in_stage2),
            'extra_in_stage2': len(extra_in_stage2),
            'coverage_percent': coverage
        }

        report = ValidationReport(
            stage="Stage1→Stage2",
            total_records=len(stage1_hashes),
            valid_records=len(stage2_hashes),
            invalid_records=len(missing_in_stage2) + len(extra_in_stage2),
            errors=errors,
            warnings=warnings,
            missing_fields_count=len(missing_in_stage2),
            extra_fields_count=len(extra_in_stage2),
            type_errors_count=0,
            value_errors_count=0
        )

        logger.info(f"Stage 1 → Stage 2 integrity check:")
        logger.info(f"  Stage 1: {len(stage1_hashes):,} unique URLs")
        logger.info(f"  Stage 2: {len(stage2_hashes):,} validated URLs")
        logger.info(f"  Coverage: {coverage:.2f}%")

        if self.fail_on_error and extra_in_stage2:
            raise ValidationFailure(
                f"Stage 2 contains {len(extra_in_stage2)} orphaned URL hashes not from Stage 1"
            )

        return report, integrity_stats

    def validate_stage2_to_stage3(
        self,
        stage2_file: Path,
        stage3_file: Path
    ) -> Tuple[ValidationReport, Dict[str, Any]]:
        """
        Validate Stage 2 → Stage 3 data integrity

        Checks:
        - All Stage 3 url_hashes exist in Stage 2
        - All Stage 3 URLs were marked as valid in Stage 2
        - Stage 3 only enriches valid URLs

        Args:
            stage2_file: Stage 2 validation output
            stage3_file: Stage 3 enrichment output

        Returns:
            Tuple of (ValidationReport, integrity_stats)
        """
        logger.info("Validating Stage 2 → Stage 3 integrity...")

        # Load Stage 2 data
        stage2_valid_hashes = set()
        stage2_all_hashes = set()
        stage2_status = {}  # hash -> is_valid

        with open(stage2_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    url_hash = data.get('url_hash')
                    is_valid = data.get('is_valid', False)

                    if url_hash:
                        stage2_all_hashes.add(url_hash)
                        stage2_status[url_hash] = is_valid
                        if is_valid:
                            stage2_valid_hashes.add(url_hash)
                except json.JSONDecodeError:
                    continue

        # Load Stage 3 hashes
        stage3_hashes = set()

        with open(stage3_file, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    url_hash = data.get('url_hash')
                    if url_hash:
                        stage3_hashes.add(url_hash)
                except json.JSONDecodeError:
                    continue

        # Check integrity
        orphaned_in_stage3 = stage3_hashes - stage2_all_hashes
        invalid_enriched = stage3_hashes - stage2_valid_hashes

        errors = []
        warnings = []

        if orphaned_in_stage3:
            errors.append({
                'error': f"{len(orphaned_in_stage3)} URL hashes in Stage 3 not found in Stage 2",
                'type': 'orphaned_hashes',
                'sample': list(orphaned_in_stage3)[:5]
            })

        if invalid_enriched:
            # These were in Stage 2 but marked invalid, yet appear in Stage 3
            actually_invalid = [h for h in invalid_enriched if h in stage2_status and not stage2_status[h]]
            if actually_invalid:
                warnings.append(
                    f"{len(actually_invalid)} URLs marked invalid in Stage 2 appear in Stage 3 "
                    f"(may be retried URLs)"
                )

        coverage = len(stage3_hashes) / len(stage2_valid_hashes) * 100 if stage2_valid_hashes else 0

        integrity_stats = {
            'stage2_valid_total': len(stage2_valid_hashes),
            'stage2_all_total': len(stage2_all_hashes),
            'stage3_total': len(stage3_hashes),
            'orphaned_in_stage3': len(orphaned_in_stage3),
            'invalid_enriched': len(invalid_enriched),
            'coverage_percent': coverage
        }

        report = ValidationReport(
            stage="Stage2→Stage3",
            total_records=len(stage2_valid_hashes),
            valid_records=len(stage3_hashes),
            invalid_records=len(orphaned_in_stage3),
            errors=errors,
            warnings=warnings,
            missing_fields_count=0,
            extra_fields_count=len(orphaned_in_stage3),
            type_errors_count=0,
            value_errors_count=0
        )

        logger.info(f"Stage 2 → Stage 3 integrity check:")
        logger.info(f"  Stage 2 valid: {len(stage2_valid_hashes):,} URLs")
        logger.info(f"  Stage 3 enriched: {len(stage3_hashes):,} URLs")
        logger.info(f"  Coverage: {coverage:.2f}%")

        if self.fail_on_error and orphaned_in_stage3:
            raise ValidationFailure(
                f"Stage 3 contains {len(orphaned_in_stage3)} orphaned URL hashes not from Stage 2"
            )

        return report, integrity_stats

    def validate_full_pipeline(
        self,
        stage1_file: Path,
        stage2_file: Path,
        stage3_file: Path
    ) -> Dict[str, Any]:
        """
        Validate complete pipeline integrity

        Args:
            stage1_file: Stage 1 discovery output
            stage2_file: Stage 2 validation output
            stage3_file: Stage 3 enrichment output

        Returns:
            Dict with comprehensive validation results
        """
        logger.info("Validating full pipeline integrity...")

        results = {
            'stage1_schema': None,
            'stage2_schema': None,
            'stage3_schema': None,
            'stage1_to_stage2': None,
            'stage2_to_stage3': None,
            'overall_status': 'PASS'
        }

        try:
            # Validate schemas
            validator1 = JSONLValidator('DiscoveryItem', fail_on_error=False)
            results['stage1_schema'] = validator1.validate_file(stage1_file)

            validator2 = JSONLValidator('ValidationResult', fail_on_error=False)
            results['stage2_schema'] = validator2.validate_file(stage2_file)

            validator3 = JSONLValidator('EnrichmentItem', fail_on_error=False)
            results['stage3_schema'] = validator3.validate_file(stage3_file)

            # Validate inter-stage integrity
            report_1_2, stats_1_2 = self.validate_stage1_to_stage2(stage1_file, stage2_file)
            results['stage1_to_stage2'] = {'report': report_1_2, 'stats': stats_1_2}

            report_2_3, stats_2_3 = self.validate_stage2_to_stage3(stage2_file, stage3_file)
            results['stage2_to_stage3'] = {'report': report_2_3, 'stats': stats_2_3}

            # Check overall status
            if not all([
                results['stage1_schema'].is_acceptable,
                results['stage2_schema'].is_acceptable,
                results['stage3_schema'].is_acceptable,
                report_1_2.is_acceptable,
                report_2_3.is_acceptable
            ]):
                results['overall_status'] = 'FAIL'

        except Exception as e:
            logger.error(f"Pipeline validation failed: {e}")
            results['overall_status'] = 'ERROR'
            results['error'] = str(e)

        return results


def validate_pipeline_output(
    stage1_file: Path,
    stage2_file: Optional[Path] = None,
    stage3_file: Optional[Path] = None,
    fail_on_error: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to validate pipeline output

    Args:
        stage1_file: Stage 1 output file
        stage2_file: Stage 2 output file (optional)
        stage3_file: Stage 3 output file (optional)
        fail_on_error: Whether to raise exception on validation failure

    Returns:
        Dict with validation results
    """
    validator = InterstageValidator(fail_on_error=fail_on_error)

    if stage3_file:
        # Full pipeline validation
        return validator.validate_full_pipeline(stage1_file, stage2_file, stage3_file)
    elif stage2_file:
        # Stage 1 → Stage 2 validation
        report, stats = validator.validate_stage1_to_stage2(stage1_file, stage2_file)
        return {'stage1_to_stage2': {'report': report, 'stats': stats}}
    else:
        # Schema validation only
        validator1 = JSONLValidator('DiscoveryItem', fail_on_error=fail_on_error)
        report = validator1.validate_file(stage1_file)
        return {'stage1_schema': report}
