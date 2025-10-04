#!/usr/bin/env python3
"""
Checkpoint Manager CLI

Provides commands for managing pipeline checkpoints:
- List checkpoints and their status
- Resume from checkpoints
- Reset checkpoints
- Export progress reports
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.enhanced_checkpoints import UnifiedCheckpointManager


def list_checkpoints(checkpoint_dir: Path):
    """List all checkpoints and their status"""
    manager = UnifiedCheckpointManager(checkpoint_dir)
    checkpoints = manager.get_all_checkpoints()

    if not checkpoints:
        print("No checkpoints found")
        return

    print("\n" + "=" * 100)
    print(f"{'Stage':<30} {'Status':<15} {'Progress':<15} {'Success Rate':<15} {'Updated':<25}")
    print("=" * 100)

    for checkpoint in checkpoints:
        report = checkpoint.get_progress_report()
        state = checkpoint.state

        progress_str = f"{report['processed']}/{report['total']} ({report['progress_pct']:.1f}%)"
        success_rate_str = f"{report['success_rate']:.1f}%"
        updated_str = state.updated_at[:19] if state.updated_at else "Unknown"

        print(f"{state.stage:<30} {state.status.value:<15} {progress_str:<15} {success_rate_str:<15} {updated_str:<25}")

    print("=" * 100 + "\n")

    # Overall summary
    pipeline_progress = manager.get_pipeline_progress()
    print(f"Overall Pipeline Progress: {pipeline_progress['progress_pct']:.1f}%")
    print(f"Active: {pipeline_progress['active_stages']} | "
          f"Completed: {pipeline_progress['completed_stages']} | "
          f"Failed: {pipeline_progress['failed_stages']}\n")


def show_checkpoint_detail(checkpoint_dir: Path, stage: str):
    """Show detailed information for a specific checkpoint"""
    manager = UnifiedCheckpointManager(checkpoint_dir)
    checkpoint = manager.get_checkpoint(stage, auto_create=False)

    if not checkpoint:
        print(f"Checkpoint not found for stage: {stage}")
        return

    state = checkpoint.state
    report = checkpoint.get_progress_report()

    print("\n" + "=" * 80)
    print(f"Checkpoint Details: {stage}")
    print("=" * 80)

    print(f"\nStatus: {state.status.value}")
    print(f"Created: {state.created_at}")
    print(f"Updated: {state.updated_at}")

    print("\nProgress:")
    print(f"  Total Items: {report['total']}")
    print(f"  Processed: {report['processed']} ({report['progress_pct']:.1f}%)")
    print(f"  Successful: {report['successful']}")
    print(f"  Failed: {report['failed']}")
    print(f"  Skipped: {report['skipped']}")

    print("\nPerformance:")
    print(f"  Success Rate: {report['success_rate']:.1f}%")
    print(f"  Throughput: {report['throughput']:.2f} items/sec")
    print(f"  Elapsed Time: {report['elapsed_seconds']:.1f} seconds")

    if report['eta_seconds']:
        print(f"  Estimated Time Remaining: {report['eta_seconds']/60:.1f} minutes")

    print("\nResume Point:")
    resume = checkpoint.get_resume_point()
    print(f"  Last Processed Index: {resume['last_processed_index']}")
    if resume['last_processed_item']:
        print(f"  Last Processed Item: {resume['last_processed_item']}")
    print(f"  Batch ID: {resume['batch_id']}")

    if state.input_file:
        print(f"\nInput File: {state.input_file}")
        if state.input_file_hash:
            print(f"  Hash: {state.input_file_hash[:16]}...")

    if state.error_message:
        print("\nError Information:")
        print(f"  Message: {state.error_message}")
        print(f"  Error Count: {state.error_count}")
        print(f"  Last Error: {state.last_error_time}")

    if state.metadata:
        print("\nMetadata:")
        for key, value in state.metadata.items():
            print(f"  {key}: {value}")

    print("=" * 80 + "\n")


def reset_checkpoint(checkpoint_dir: Path, stage: str, force: bool = False):
    """Reset a checkpoint"""
    manager = UnifiedCheckpointManager(checkpoint_dir)
    checkpoint = manager.get_checkpoint(stage, auto_create=False)

    if not checkpoint:
        print(f"Checkpoint not found for stage: {stage}")
        return

    if not force:
        response = input(f"Are you sure you want to reset checkpoint for '{stage}'? [y/N]: ")
        if response.lower() != 'y':
            print("Reset cancelled")
            return

    checkpoint.reset()
    print(f"Checkpoint reset: {stage}")


def reset_all_checkpoints(checkpoint_dir: Path, force: bool = False):
    """Reset all checkpoints"""
    manager = UnifiedCheckpointManager(checkpoint_dir)

    if not force:
        response = input("Are you sure you want to reset ALL checkpoints? [y/N]: ")
        if response.lower() != 'y':
            print("Reset cancelled")
            return

    manager.reset_all()
    print("All checkpoints reset")


def export_report(checkpoint_dir: Path, output_file: Path):
    """Export progress report to JSON"""
    manager = UnifiedCheckpointManager(checkpoint_dir)
    manager.export_report(output_file)
    print(f"Progress report exported to: {output_file}")


def cleanup_old(checkpoint_dir: Path, keep_days: int):
    """Clean up old checkpoint files"""
    manager = UnifiedCheckpointManager(checkpoint_dir)
    manager.cleanup_old_checkpoints(keep_days=keep_days)
    print(f"Cleaned up checkpoints older than {keep_days} days")


def print_progress_report(checkpoint_dir: Path):
    """Print comprehensive progress report"""
    manager = UnifiedCheckpointManager(checkpoint_dir)
    manager.print_progress_report()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Checkpoint Manager CLI - Manage pipeline checkpoints',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all checkpoints
  python tools/checkpoint_manager_cli.py list

  # Show details for specific stage
  python tools/checkpoint_manager_cli.py show stage1_discovery

  # Reset a checkpoint
  python tools/checkpoint_manager_cli.py reset stage2_validation

  # Export progress report
  python tools/checkpoint_manager_cli.py export --output report.json

  # Clean up old checkpoints
  python tools/checkpoint_manager_cli.py cleanup --days 7
        """
    )

    parser.add_argument(
        '--checkpoint-dir',
        type=Path,
        default=Path('data/checkpoints'),
        help='Checkpoint directory (default: data/checkpoints)'
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # List command
    subparsers.add_parser('list', help='List all checkpoints')

    # Show command
    show_parser = subparsers.add_parser('show', help='Show checkpoint details')
    show_parser.add_argument('stage', help='Stage name')

    # Reset command
    reset_parser = subparsers.add_parser('reset', help='Reset a checkpoint')
    reset_parser.add_argument('stage', help='Stage name (or "all" for all checkpoints)')
    reset_parser.add_argument('--force', action='store_true', help='Skip confirmation')

    # Export command
    export_parser = subparsers.add_parser('export', help='Export progress report')
    export_parser.add_argument(
        '--output',
        type=Path,
        default=Path(f'checkpoint_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'),
        help='Output file path'
    )

    # Cleanup command
    cleanup_parser = subparsers.add_parser('cleanup', help='Clean up old checkpoints')
    cleanup_parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='Keep checkpoints newer than this many days (default: 7)'
    )

    # Report command
    subparsers.add_parser('report', help='Print comprehensive progress report')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Execute command
    if args.command == 'list':
        list_checkpoints(args.checkpoint_dir)

    elif args.command == 'show':
        show_checkpoint_detail(args.checkpoint_dir, args.stage)

    elif args.command == 'reset':
        if args.stage == 'all':
            reset_all_checkpoints(args.checkpoint_dir, args.force)
        else:
            reset_checkpoint(args.checkpoint_dir, args.stage, args.force)

    elif args.command == 'export':
        export_report(args.checkpoint_dir, args.output)

    elif args.command == 'cleanup':
        cleanup_old(args.checkpoint_dir, args.days)

    elif args.command == 'report':
        print_progress_report(args.checkpoint_dir)


if __name__ == '__main__':
    main()
