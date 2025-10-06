#!/usr/bin/env python3
"""
Health Check Endpoint
Verifies that the pipeline components are healthy and accessible.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))


def check_delta_lake():
    """Check Delta Lake connection."""
    try:
        from src.common.delta_lake import get_delta_manager
        delta = get_delta_manager()
        # Verify tables exist
        assert delta.base_path.exists(), "Delta Lake base path does not exist"
        return True
    except Exception as e:
        print(f"❌ Delta Lake check failed: {e}")
        return False


def check_dependencies():
    """Check critical dependencies are installed."""
    required = [
        'scrapy',
        'aiohttp',
        'datasketch',
        'bs4',
        'yake',
    ]

    missing = []
    for package in required:
        try:
            __import__(package)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"❌ Missing dependencies: {', '.join(missing)}")
        return False

    return True


def check_file_structure():
    """Check critical files exist."""
    critical_files = [
        'run_pipeline.py',
        'src/stage1/scout_spider.py',
        'src/stage1/js_bot.py',
        'src/stage2/stage2_worker.py',
        'src/stage3/stage3_worker.py',
        'src/common/delta_lake.py',
    ]

    base_path = Path(__file__).parent

    missing = []
    for file_path in critical_files:
        if not (base_path / file_path).exists():
            missing.append(file_path)

    if missing:
        print(f"❌ Missing critical files: {', '.join(missing)}")
        return False

    return True


def main():
    """Run all health checks."""
    print("Running health checks...")

    checks = [
        ("File Structure", check_file_structure),
        ("Dependencies", check_dependencies),
        ("Delta Lake", check_delta_lake),
    ]

    all_passed = True

    for name, check_func in checks:
        print(f"\nChecking {name}...", end=' ')
        try:
            if check_func():
                print("✅ PASSED")
            else:
                print("❌ FAILED")
                all_passed = False
        except Exception as e:
            print(f"❌ FAILED: {e}")
            all_passed = False

    print("\n" + "=" * 50)
    if all_passed:
        print("✅ All health checks passed!")
        return 0
    else:
        print("❌ Some health checks failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
