#!/usr/bin/env python3
"""
CLI tool to validate pipeline data integrity.
Run after each stage or at the end to verify data quality.
"""

import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.interstage_validation import validate_pipeline_output


def main():
    parser = argparse.ArgumentParser(
        description="Validate pipeline data integrity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Validate Stage 1 output schema
  python tools/validate_pipeline_data.py --stage1 data/processed/stage01/discovery_output.jsonl

  # Validate Stage 1 → Stage 2 integrity
  python tools/validate_pipeline_data.py --stage1 data/processed/stage01/discovery_output.jsonl \\
                                         --stage2 data/processed/stage02/validated_urls.jsonl

  # Validate full pipeline
  python tools/validate_pipeline_data.py --stage1 data/processed/stage01/discovery_output.jsonl \\
                                         --stage2 data/processed/stage02/validated_urls.jsonl \\
                                         --stage3 data/processed/stage03/enrichment_output.jsonl

  # Fail on validation errors (exit code 1)
  python tools/validate_pipeline_data.py --stage1 ... --fail-on-error
        """
    )

    parser.add_argument(
        '--stage1',
        type=Path,
        required=True,
        help='Path to Stage 1 discovery output (JSONL)'
    )
    parser.add_argument(
        '--stage2',
        type=Path,
        help='Path to Stage 2 validation output (JSONL)'
    )
    parser.add_argument(
        '--stage3',
        type=Path,
        help='Path to Stage 3 enrichment output (JSONL)'
    )
    parser.add_argument(
        '--fail-on-error',
        action='store_true',
        help='Exit with error code if validation fails'
    )
    parser.add_argument(
        '--sample-rate',
        type=float,
        default=1.0,
        help='Sample rate for validation (0.0-1.0, default 1.0 = all records)'
    )

    args = parser.parse_args()

    # Validate files exist
    if not args.stage1.exists():
        print(f"❌ Error: Stage 1 file not found: {args.stage1}")
        sys.exit(1)

    if args.stage2 and not args.stage2.exists():
        print(f"❌ Error: Stage 2 file not found: {args.stage2}")
        sys.exit(1)

    if args.stage3 and not args.stage3.exists():
        print(f"❌ Error: Stage 3 file not found: {args.stage3}")
        sys.exit(1)

    # Run validation
    try:
        results = validate_pipeline_output(
            stage1_file=args.stage1,
            stage2_file=args.stage2,
            stage3_file=args.stage3,
            fail_on_error=args.fail_on_error
        )

        # Print results
        print("\n" + "=" * 60)
        print("PIPELINE DATA VALIDATION RESULTS")
        print("=" * 60)

        if 'stage1_schema' in results and results['stage1_schema']:
            print(results['stage1_schema'].summary())

        if 'stage2_schema' in results and results['stage2_schema']:
            print(results['stage2_schema'].summary())

        if 'stage3_schema' in results and results['stage3_schema']:
            print(results['stage3_schema'].summary())

        if 'stage1_to_stage2' in results and results['stage1_to_stage2']:
            print(results['stage1_to_stage2']['report'].summary())
            stats = results['stage1_to_stage2']['stats']
            print(f"Coverage: {stats['coverage_percent']:.2f}%")
            print(f"Stage 1: {stats['stage1_total']:,} URLs")
            print(f"Stage 2: {stats['stage2_total']:,} URLs")
            print()

        if 'stage2_to_stage3' in results and results['stage2_to_stage3']:
            print(results['stage2_to_stage3']['report'].summary())
            stats = results['stage2_to_stage3']['stats']
            print(f"Coverage: {stats['coverage_percent']:.2f}%")
            print(f"Stage 2 valid: {stats['stage2_valid_total']:,} URLs")
            print(f"Stage 3 enriched: {stats['stage3_total']:,} URLs")
            print()

        overall_status = results.get('overall_status', 'UNKNOWN')
        if overall_status == 'PASS':
            print("✅ Overall Status: PASS - All validation checks passed")
            sys.exit(0)
        elif overall_status == 'FAIL':
            print("❌ Overall Status: FAIL - Some validation checks failed")
            if args.fail_on_error:
                sys.exit(1)
            sys.exit(0)
        else:
            print(f"⚠️  Overall Status: {overall_status}")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Validation error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
