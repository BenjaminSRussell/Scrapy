#!/usr/bin/env python3
"""
Data Refresh CLI Tool

Command-line interface for intelligent data refresh and recheck operations.
Provides comprehensive options for updating scraped data with learning capabilities.

Usage Examples:
    python3 refresh_data.py --status                    # Check refresh status
    python3 refresh_data.py --refresh-validation        # Refresh failed/old validation data
    python3 refresh_data.py --force-all                 # Force refresh all data
    python3 refresh_data.py --analytics                 # Generate analytics report
    python3 refresh_data.py --optimize                  # Get optimization recommendations
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.analytics_engine import RequestAnalyticsEngine
from orchestrator.data_refresh import DataRefreshManager, RefreshConfig


def setup_cli_logging(verbose: bool = False):
    """Setup logging for CLI"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )


def print_status_summary(status: dict):
    """Print formatted status summary"""
    print("=" * 60)
    print("📊 DATA REFRESH STATUS")
    print("=" * 60)

    print(f"📋 Total URLs: {status['total_urls']:,}")
    print(f"⚠️  High Priority: {status['high_priority_count']:,}")
    print(f"❌ Failed Items: {status['failed_count']:,}")

    if status['last_refresh']:
        last_refresh = datetime.fromisoformat(status['last_refresh'])
        hours_ago = (datetime.now() - last_refresh).total_seconds() / 3600
        print(f"🕒 Last Refresh: {hours_ago:.1f} hours ago")
    else:
        print("🕒 Last Refresh: Never")

    recommendations = status['recommendations']

    print("\n🎯 RECOMMENDATIONS:")
    if recommendations['should_refresh']:
        print("✅ Refresh recommended")
        print(f"⏱️  Estimated time: ~{recommendations['estimated_time_minutes']} minutes")

        if recommendations['high_priority_domains']:
            print(f"🏗️  Priority domains: {', '.join(recommendations['high_priority_domains'][:5])}")
    else:
        print("✅ No immediate refresh needed")


def print_refresh_results(results: dict):
    """Print formatted refresh results"""
    print("=" * 60)
    print("🔄 REFRESH RESULTS")
    print("=" * 60)

    print(f"📋 Total Processed: {results['total_processed']:,}")
    print(f"✅ Successful: {results['successful']:,}")
    print(f"❌ Failed: {results['failed']:,}")
    print(f"🔄 Changed: {results['changed']:,}")
    print(f"⏱️  Processing Time: {results['processing_time']:.1f}s")

    if 'performance_summary' in results:
        perf = results['performance_summary']
        print("\n📈 PERFORMANCE:")
        print(f"   Success Rate: {perf['success_rate']}")
        print(f"   Avg Retries: {perf['avg_retries_per_request']:.1f}")
        print(f"   Domains Learned: {perf['domains_learned']}")


def print_analytics_summary(dashboard: dict):
    """Print analytics dashboard summary"""
    if 'error' in dashboard:
        print(f"❌ Analytics Error: {dashboard['error']}")
        return

    overview = dashboard['overview']

    print("=" * 60)
    print("📊 ANALYTICS DASHBOARD")
    print("=" * 60)

    print(f"📋 Total Requests: {overview['total_requests']:,}")
    print(f"✅ Success Rate: {overview['success_rate']:.1f}%")
    print(f"⏱️  Avg Response Time: {overview['avg_response_time']:.2f}s")
    print(f"📅 Period: {overview['time_period']}")

    # Top domains
    print("\n🏗️  TOP DOMAINS:")
    for i, domain_data in enumerate(dashboard['domain_performance'][:5], 1):
        print(f"   {i}. {domain_data['domain']}: {domain_data['requests']} requests, {domain_data['success_rate']:.1f}% success")

    # Error breakdown
    if dashboard['error_breakdown']:
        print("\n❌ TOP ERRORS:")
        for i, (error, count) in enumerate(list(dashboard['error_breakdown'].items())[:5], 1):
            print(f"   {i}. {error}: {count} occurrences")

    # Patterns
    patterns = dashboard['identified_patterns']
    if patterns:
        print(f"\n🔍 IDENTIFIED PATTERNS ({len(patterns)}):")
        for pattern in patterns[:3]:
            print(f"   • {pattern['description']} (confidence: {pattern['confidence']:.0%})")


def print_optimization_recommendations(recommendations: dict):
    """Print optimization recommendations"""
    print("=" * 60)
    print("🚀 OPTIMIZATION RECOMMENDATIONS")
    print("=" * 60)

    immediate = recommendations.get('immediate_actions', [])
    if immediate:
        print("⚡ IMMEDIATE ACTIONS:")
        for i, action in enumerate(immediate, 1):
            print(f"   {i}. {action}")

    config_changes = recommendations.get('configuration_changes', {})
    if config_changes:
        print("\n⚙️  CONFIGURATION CHANGES:")
        for change_type, changes in config_changes.items():
            print(f"   {change_type.replace('_', ' ').title()}:")
            if isinstance(changes, dict):
                for domain, value in list(changes.items())[:5]:
                    print(f"     - {domain}: {value}")
                if len(changes) > 5:
                    print(f"     ... and {len(changes) - 5} more")

    monitoring = recommendations.get('monitoring_alerts', [])
    if monitoring:
        print("\n📊 MONITORING ALERTS:")
        for i, alert in enumerate(monitoring, 1):
            print(f"   {i}. {alert}")

    performance = recommendations.get('performance_optimizations', [])
    if performance:
        print("\n📈 PERFORMANCE OPTIMIZATIONS:")
        for i, opt in enumerate(performance, 1):
            print(f"   {i}. {opt}")


async def main():
    """Main CLI function"""
    parser = argparse.ArgumentParser(
        description="Intelligent Data Refresh Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --status                    Check current refresh status
  %(prog)s --refresh-validation        Refresh validation data (smart)
  %(prog)s --refresh-validation --force-all    Force refresh all validation data
  %(prog)s --analytics                 Generate analytics dashboard
  %(prog)s --optimize                  Get optimization recommendations
  %(prog)s --export-analytics report.json     Export detailed analytics report
        """
    )

    # Main actions
    parser.add_argument('--status', action='store_true',
                      help='Show current refresh status and recommendations')
    parser.add_argument('--refresh-validation', action='store_true',
                      help='Refresh validation data (smart refresh by default)')
    parser.add_argument('--refresh-all', action='store_true',
                      help='Refresh all data stages')
    parser.add_argument('--analytics', action='store_true',
                      help='Show analytics dashboard')
    parser.add_argument('--optimize', action='store_true',
                      help='Show optimization recommendations')

    # Modifiers
    parser.add_argument('--force-all', action='store_true',
                      help='Force refresh all data (not just high priority)')
    parser.add_argument('--max-concurrent', type=int, default=20,
                      help='Maximum concurrent requests (default: 20)')
    parser.add_argument('--priority-domains', nargs='+',
                      help='Domains to prioritize for refresh')

    # Output options
    parser.add_argument('--export-analytics', metavar='FILE',
                      help='Export detailed analytics to JSON file')
    parser.add_argument('--output-format', choices=['text', 'json'], default='text',
                      help='Output format (default: text)')

    # Logging
    parser.add_argument('--verbose', '-v', action='store_true',
                      help='Enable verbose logging')
    parser.add_argument('--quiet', '-q', action='store_true',
                      help='Suppress non-essential output')

    args = parser.parse_args()

    # Setup logging
    if not args.quiet:
        setup_cli_logging(args.verbose)

    # Validate arguments
    if not any([args.status, args.refresh_validation, args.refresh_all, args.analytics, args.optimize, args.export_analytics]):
        parser.print_help()
        return 1

    try:
        # Create refresh configuration
        refresh_config = RefreshConfig(
            max_concurrent=args.max_concurrent,
            priority_domains=args.priority_domains,
            force_refresh_failed=True,
            create_incremental_files=True,
            backup_existing=True
        )

        # Show status
        if args.status:
            async with DataRefreshManager(refresh_config) as refresh_manager:
                status = refresh_manager.get_refresh_status()

                if args.output_format == 'json':
                    print(json.dumps(status, indent=2))
                else:
                    print_status_summary(status)

        # Refresh validation data
        if args.refresh_validation:
            print("🔄 Starting validation data refresh...")

            async with DataRefreshManager(refresh_config) as refresh_manager:
                results = await refresh_manager.refresh_validation_data(force_all=args.force_all)

                if args.output_format == 'json':
                    print(json.dumps(results, indent=2))
                else:
                    print_refresh_results(results)

        # Refresh all data
        if args.refresh_all:
            print("🔄 Starting full data refresh...")

            async with DataRefreshManager(refresh_config) as refresh_manager:
                results = await refresh_manager.full_data_refresh()

                if args.output_format == 'json':
                    print(json.dumps(results, indent=2))
                else:
                    for stage, stage_results in results.items():
                        print(f"\n📋 {stage.upper()} STAGE:")
                        print_refresh_results(stage_results)

        # Show analytics
        if args.analytics:
            analytics_engine = RequestAnalyticsEngine()
            dashboard = analytics_engine.get_performance_dashboard()

            if args.output_format == 'json':
                print(json.dumps(dashboard, indent=2))
            else:
                print_analytics_summary(dashboard)

        # Show optimization recommendations
        if args.optimize:
            analytics_engine = RequestAnalyticsEngine()
            recommendations = analytics_engine.generate_optimization_recommendations()

            if args.output_format == 'json':
                print(json.dumps(recommendations, indent=2))
            else:
                print_optimization_recommendations(recommendations)

        # Export analytics
        if args.export_analytics:
            analytics_engine = RequestAnalyticsEngine()
            output_file = Path(args.export_analytics)
            exported_file = analytics_engine.export_analytics_report(output_file)

            if not args.quiet:
                print(f"📄 Analytics report exported to: {exported_file}")

        return 0

    except KeyboardInterrupt:
        if not args.quiet:
            print("\n⚠️  Operation cancelled by user")
        return 1

    except Exception as e:
        if args.verbose:
            import traceback
            traceback.print_exc()
        else:
            print(f"❌ Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))