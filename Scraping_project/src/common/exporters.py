"""Data export utilities for various formats."""

import csv
import json
from pathlib import Path
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class CSVExporter:
    """Export pipeline data to CSV format."""

    def __init__(self, output_file: Path):
        self.output_file = Path(output_file)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

    def export_jsonl_to_csv(self, jsonl_file: Path, fields: List[str] = None):
        """Convert JSONL file to CSV."""
        if not jsonl_file.exists():
            raise FileNotFoundError(f"JSONL file not found: {jsonl_file}")

        # Read first few lines to determine fields if not provided
        if fields is None:
            fields = self._detect_fields(jsonl_file)

        logger.info(f"Exporting {jsonl_file} to {self.output_file}")
        logger.info(f"Fields: {', '.join(fields)}")

        rows_exported = 0
        with open(jsonl_file, 'r', encoding='utf-8') as infile:
            with open(self.output_file, 'w', newline='', encoding='utf-8') as outfile:
                writer = csv.DictWriter(outfile, fieldnames=fields, extrasaction='ignore')
                writer.writeheader()

                for line_no, line in enumerate(infile, 1):
                    try:
                        data = json.loads(line.strip())

                        # Flatten complex fields
                        flattened = self._flatten_data(data)
                        writer.writerow(flattened)
                        rows_exported += 1

                        if rows_exported % 1000 == 0:
                            logger.info(f"Exported {rows_exported} rows")

                    except json.JSONDecodeError as e:
                        logger.warning(f"Skipping invalid JSON at line {line_no}: {e}")
                        continue

        logger.info(f"CSV export completed: {rows_exported} rows exported to {self.output_file}")
        return rows_exported

    def _detect_fields(self, jsonl_file: Path) -> List[str]:
        """Detect fields from first few lines of JSONL file."""
        fields = set()

        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i >= 5:  # Check first 5 lines
                    break
                try:
                    data = json.loads(line.strip())
                    flattened = self._flatten_data(data)
                    fields.update(flattened.keys())
                except json.JSONDecodeError:
                    continue

        return sorted(list(fields))

    def _flatten_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten nested data for CSV export."""
        flattened = {}

        for key, value in data.items():
            if isinstance(value, list):
                # Convert lists to semicolon-separated strings
                flattened[key] = ';'.join(str(item) for item in value) if value else ''
            elif isinstance(value, dict):
                # Convert dicts to JSON strings
                flattened[key] = json.dumps(value)
            elif value is None:
                flattened[key] = ''
            else:
                flattened[key] = str(value)

        return flattened


class JSONExporter:
    """Export pipeline data to structured JSON format."""

    def __init__(self, output_file: Path):
        self.output_file = Path(output_file)
        self.output_file.parent.mkdir(parents=True, exist_ok=True)

    def export_jsonl_to_json(self, jsonl_file: Path, pretty: bool = True):
        """Convert JSONL file to structured JSON."""
        if not jsonl_file.exists():
            raise FileNotFoundError(f"JSONL file not found: {jsonl_file}")

        logger.info(f"Converting {jsonl_file} to structured JSON")

        data = []
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line_no, line in enumerate(f, 1):
                try:
                    item = json.loads(line.strip())
                    data.append(item)
                except json.JSONDecodeError as e:
                    logger.warning(f"Skipping invalid JSON at line {line_no}: {e}")
                    continue

        with open(self.output_file, 'w', encoding='utf-8') as f:
            if pretty:
                json.dump(data, f, indent=2, ensure_ascii=False)
            else:
                json.dump(data, f, ensure_ascii=False)

        logger.info(f"JSON export completed: {len(data)} items exported to {self.output_file}")
        return len(data)


class ReportGenerator:
    """Generate summary reports from pipeline data."""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_pipeline_report(self, stage1_file: Path, stage2_file: Path, stage3_file: Path):
        """Generate comprehensive pipeline report."""
        report = {
            "generated_at": json.loads(json.dumps(None, default=str)),  # Current timestamp
            "stage1_summary": self._analyze_stage_file(stage1_file, "discovery"),
            "stage2_summary": self._analyze_stage_file(stage2_file, "validation"),
            "stage3_summary": self._analyze_stage_file(stage3_file, "enrichment")
        }

        # Calculate conversion rates
        stage1_count = report["stage1_summary"]["total_items"]
        stage2_count = report["stage2_summary"]["total_items"]
        stage3_count = report["stage3_summary"]["total_items"]

        report["conversion_rates"] = {
            "stage1_to_stage2": (stage2_count / max(1, stage1_count)) * 100,
            "stage2_to_stage3": (stage3_count / max(1, stage2_count)) * 100,
            "end_to_end": (stage3_count / max(1, stage1_count)) * 100
        }

        # Write report
        report_file = self.output_dir / "pipeline_report.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, default=str)

        logger.info(f"Pipeline report generated: {report_file}")
        return report

    def _analyze_stage_file(self, file_path: Path, stage_type: str) -> Dict[str, Any]:
        """Analyze a single stage file."""
        if not file_path.exists():
            return {"total_items": 0, "error": f"File not found: {file_path}"}

        total_items = 0
        valid_items = 0
        content_types = {}
        url_patterns = {}

        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                try:
                    data = json.loads(line.strip())
                    total_items += 1

                    # Stage-specific analysis
                    if stage_type == "validation" and data.get("is_valid"):
                        valid_items += 1
                        content_type = data.get("content_type", "unknown")
                        content_types[content_type] = content_types.get(content_type, 0) + 1

                    # URL pattern analysis
                    url = data.get("url") or data.get("discovered_url", "")
                    if url:
                        domain = url.split('/')[2] if '://' in url else "unknown"
                        url_patterns[domain] = url_patterns.get(domain, 0) + 1

                except json.JSONDecodeError:
                    continue

        return {
            "total_items": total_items,
            "valid_items": valid_items if stage_type == "validation" else total_items,
            "success_rate": (valid_items / max(1, total_items)) * 100 if stage_type == "validation" else 100,
            "content_types": dict(sorted(content_types.items(), key=lambda x: x[1], reverse=True)[:10]),
            "top_domains": dict(sorted(url_patterns.items(), key=lambda x: x[1], reverse=True)[:10])
        }