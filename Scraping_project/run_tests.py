#!/usr/bin/env python3
"""
Production Test Runner for UConn Scraping Pipeline
Runs comprehensive test suite with different profiles for various use cases.
"""

import subprocess
import sys
import time
import json
from pathlib import Path
from typing import Dict, Any, List

def run_test_profile(profile: str, extra_args: List[str] = None) -> Dict[str, Any]:
    """Run a specific test profile and return results"""
    extra_args = extra_args or []

    profiles = {
        "quick": [
            "-m", "unit and not slow",
            "--maxfail=5",
            "-q"
        ],
        "unit": [
            "-m", "unit",
            "--maxfail=10"
        ],
        "integration": [
            "-m", "integration",
            "--maxfail=3"
        ],
        "performance": [
            "-m", "performance",
            "--tb=line",
            "-s"
        ],
        "critical": [
            "-m", "critical",
            "--maxfail=1",
            "-x"
        ],
        "full": [
            "--maxfail=5"
        ],
        "load": [
            "-m", "load or slow",
            "--tb=line",
            "-s"
        ]
    }

    if profile not in profiles:
        raise ValueError(f"Unknown profile: {profile}. Available: {list(profiles.keys())}")

    # Base pytest command
    cmd = ["python3", "-m", "pytest"] + profiles[profile] + extra_args

    print(f"🚀 Running {profile} test profile...")
    print(f"📋 Command: {' '.join(cmd)}")
    print("=" * 60)

    start_time = time.perf_counter()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800  # 30 minute timeout
        )

        duration = time.perf_counter() - start_time

        return {
            "profile": profile,
            "success": result.returncode == 0,
            "duration": duration,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "command": " ".join(cmd)
        }

    except subprocess.TimeoutExpired:
        duration = time.perf_counter() - start_time
        return {
            "profile": profile,
            "success": False,
            "duration": duration,
            "returncode": -1,
            "stdout": "",
            "stderr": "Test execution timed out after 30 minutes",
            "command": " ".join(cmd)
        }

def parse_pytest_output(output: str) -> Dict[str, Any]:
    """Parse pytest output to extract metrics"""
    lines = output.split('\n')

    metrics = {
        "tests_run": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "warnings": 0,
        "duration": 0.0,
        "slowest_tests": []
    }

    for line in lines:
        # Parse test results summary
        if "passed" in line and ("failed" in line or "error" in line or "skipped" in line):
            # Example: "5 failed, 10 passed, 2 skipped in 1.23s"
            parts = line.split()
            for i, part in enumerate(parts):
                if part.isdigit():
                    count = int(part)
                    if i + 1 < len(parts):
                        if "passed" in parts[i + 1]:
                            metrics["passed"] = count
                        elif "failed" in parts[i + 1]:
                            metrics["failed"] = count
                        elif "skipped" in parts[i + 1]:
                            metrics["skipped"] = count
                        elif "error" in parts[i + 1]:
                            metrics["errors"] = count

        # Parse duration
        if " in " in line and line.endswith("s"):
            try:
                duration_str = line.split(" in ")[-1].replace("s", "")
                metrics["duration"] = float(duration_str)
            except:
                pass

        # Parse slowest tests
        if "slowest durations" in line.lower():
            # TODO: Parse slowest test information
            pass

    metrics["tests_run"] = metrics["passed"] + metrics["failed"] + metrics["skipped"] + metrics["errors"]

    return metrics

def generate_test_report(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Generate comprehensive test report with production readiness assessment"""
    total_duration = sum(r["duration"] for r in results)
    successful_profiles = [r for r in results if r["success"]]
    failed_profiles = [r for r in results if not r["success"]]

    # Parse pytest metrics for each profile
    profile_metrics = {}
    for result in results:
        metrics = parse_pytest_output(result["stdout"])
        profile_metrics[result["profile"]] = metrics

    # Calculate overall metrics
    total_tests = sum(m["tests_run"] for m in profile_metrics.values())
    total_passed = sum(m["passed"] for m in profile_metrics.values())
    total_failed = sum(m["failed"] for m in profile_metrics.values())

    # Production readiness assessment
    try:
        from tests.production_readiness import (
            ProductionReadinessChecker,
            get_system_resources,
        )

        checker = ProductionReadinessChecker()

        # Build test results for readiness evaluation
        test_results = {}
        for result in results:
            test_results[result["profile"]] = {
                "success": result["success"],
                "metrics": profile_metrics.get(result["profile"], {})
            }

        readiness_assessment = checker.evaluate_readiness(test_results)
        system_resources = get_system_resources()

    except ImportError:
        readiness_assessment = {
            "ready_for_production": False,
            "readiness_percentage": 0,
            "error": "Production readiness checker not available"
        }
        system_resources = {}

    report = {
        "summary": {
            "profiles_run": len(results),
            "successful_profiles": len(successful_profiles),
            "failed_profiles": len(failed_profiles),
            "success_rate": len(successful_profiles) / len(results) * 100 if results else 0,
            "total_duration": total_duration,
            "total_tests": total_tests,
            "total_passed": total_passed,
            "total_failed": total_failed,
            "overall_pass_rate": total_passed / max(1, total_tests) * 100
        },
        "profile_results": {r["profile"]: {
            "success": r["success"],
            "duration": r["duration"],
            "metrics": profile_metrics.get(r["profile"], {})
        } for r in results},
        "failed_profiles": [r["profile"] for r in failed_profiles],
        "production_readiness": readiness_assessment,
        "system_resources": system_resources,
        "detailed_results": results
    }

    return report

def main():
    """Main test runner"""
    import argparse

    parser = argparse.ArgumentParser(description="UConn Pipeline Test Runner")
    parser.add_argument("profile", nargs="?", default="quick",
                      choices=["quick", "unit", "integration", "performance", "critical", "full", "load", "all"],
                      help="Test profile to run")
    parser.add_argument("--standalone", action="store_true",
                      help="Run standalone test runner instead of pytest")
    parser.add_argument("--report", type=str,
                      help="Save JSON report to file")
    parser.add_argument("--verbose", "-v", action="store_true",
                      help="Verbose output")
    parser.add_argument("--fail-fast", "-x", action="store_true",
                      help="Stop on first failure")

    args = parser.parse_args()

    if args.standalone:
        # Run the standalone test runner
        print("🔧 Running standalone test runner...")
        try:
            import test_runner
            return test_runner.main()
        except ImportError as e:
            print(f"❌ Could not import standalone test runner: {e}")
            return 1

    # Prepare extra arguments
    extra_args = []
    if args.verbose:
        extra_args.append("-v")
    if args.fail_fast:
        extra_args.append("-x")

    # Run test profiles
    if args.profile == "all":
        profiles_to_run = ["critical", "unit", "integration", "performance"]
    else:
        profiles_to_run = [args.profile]

    print(f"🧪 UConn Scraping Pipeline Test Suite")
    print(f"📋 Running profiles: {', '.join(profiles_to_run)}")
    print("=" * 60)

    results = []
    overall_success = True

    for profile in profiles_to_run:
        result = run_test_profile(profile, extra_args)
        results.append(result)

        if result["success"]:
            print(f"✅ {profile.upper()} tests PASSED ({result['duration']:.1f}s)")
        else:
            print(f"❌ {profile.upper()} tests FAILED ({result['duration']:.1f}s)")
            if args.verbose:
                print(f"   STDERR: {result['stderr']}")
            overall_success = False

        print()

    # Generate report
    report = generate_test_report(results)

    print("=" * 60)
    print("📊 TEST EXECUTION SUMMARY")
    print("=" * 60)

    summary = report["summary"]
    print(f"🎯 Profiles: {summary['successful_profiles']}/{summary['profiles_run']} successful")
    print(f"🧪 Tests: {summary['total_passed']}/{summary['total_tests']} passed ({summary['overall_pass_rate']:.1f}%)")
    print(f"⏱️  Duration: {summary['total_duration']:.1f}s")

    if report["failed_profiles"]:
        print(f"❌ Failed profiles: {', '.join(report['failed_profiles'])}")

    # Enhanced production readiness assessment
    print("\n" + "=" * 60)
    print("🚀 PRODUCTION READINESS ASSESSMENT")
    print("=" * 60)

    readiness = report.get("production_readiness", {})
    if "error" in readiness:
        print(f"⚠️  Assessment unavailable: {readiness['error']}")
    else:
        readiness_pct = readiness.get("readiness_percentage", 0)
        is_ready = readiness.get("ready_for_production", False)

        status_icon = "✅" if is_ready else "❌"
        print(f"{status_icon} Production Readiness: {readiness_pct:.1f}%")

        criteria = readiness.get("criteria_met", {})
        for criterion, met in criteria.items():
            icon = "✅" if met else "❌"
            readable_name = criterion.replace("_", " ").title()
            print(f"  {icon} {readable_name}")

        recommendations = readiness.get("recommendations", [])
        if recommendations:
            print("\n📋 Recommendations:")
            for rec in recommendations:
                print(f"  {rec}")

    # System resources
    resources = report.get("system_resources", {})
    if resources:
        print(f"\n💻 System Resources:")
        print(f"  Memory: {resources.get('memory_available_gb', 0):.1f}GB available / {resources.get('memory_total_gb', 0):.1f}GB total")
        print(f"  CPU: {resources.get('cpu_count', 0)} cores")
        print(f"  Process Memory: {resources.get('process_memory_mb', 0):.1f}MB")

    # Save report if requested
    if args.report:
        report_path = Path(args.report)
        with open(report_path, 'w') as f:
            json.dump(report, f, indent=2)
        print(f"\n📄 Detailed report saved to: {report_path}")

    return 0 if overall_success else 1

if __name__ == "__main__":
    sys.exit(main())
