
from __future__ import annotations

import logging
from pathlib import Path

from src.common.interstage_validation import JSONLValidator, ValidationReport

logger = logging.getLogger(__name__)

def validate_stage_output(stage: int, filepath: str | Path, sample_rate: float = 0.1) -> ValidationReport | None:
    """
    Validates the output of a pipeline stage against its schema.

    Args:
        stage: The stage number (1, 2, or 3).
        filepath: The path to the output file.
        sample_rate: The fraction of records to validate.

    Returns:
        A ValidationReport, or None if validation is skipped or fails unexpectedly.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        logger.warning(f"Validation skipped: File not found at {filepath}")
        return None

    schema_map = {
        1: "DiscoveryItem",
        2: "ValidationResult",
        3: "EnrichmentItem",
    }

    schema_name = schema_map.get(stage)
    if not schema_name:
        logger.error(f"Invalid stage number for validation: {stage}")
        return None

    try:
        validator = JSONLValidator(schema_name, fail_on_error=False, sample_rate=sample_rate)
        report = validator.validate_file(filepath)
        
        if not report.is_acceptable:
            logger.warning(f"Stage {stage} output validation failed with a success rate of {report.success_rate:.2f}%.")
        else:
            logger.info(f"Stage {stage} output validation passed with a success rate of {report.success_rate:.2f}%.")
            
        return report
    except Exception as e:
        logger.error(f"An unexpected error occurred during validation of stage {stage} output: {e}")
        return None
