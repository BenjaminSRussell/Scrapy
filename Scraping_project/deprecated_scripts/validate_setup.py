#!/usr/bin/env python3
"""Validate Pipeline Setup - Check all components are ready"""

import sys
from pathlib import Path


def check_file(path: Path, description: str) -> bool:
    """Check if a file exists."""
    if path.exists():
        print(f"  ✅ {description}")
        return True
    else:
        print(f"  ❌ {description} - NOT FOUND: {path}")
        return False


def check_directory(path: Path, description: str) -> bool:
    """Check if a directory exists."""
    if path.is_dir():
        print(f"  ✅ {description}")
        return True
    else:
        print(f"  ⚠️  {description} - NOT FOUND (will be created): {path}")
        return True  # Directories are auto-created


def check_seed_file(path: Path) -> bool:
    """Check seed file and count URLs."""
    if not path.exists():
        print(f"  ❌ Seed file NOT FOUND: {path}")
        return False

    with open(path) as f:
        urls = [line.strip() for line in f if line.strip() and line.strip().startswith('http')]

    print(f"  ✅ Seed file exists: {len(urls):,} URLs")
    return len(urls) > 0


def main():
    """Validate complete setup."""
    project_root = Path(__file__).parent
    all_ok = True

    print("=" * 80)
    print("PIPELINE SETUP VALIDATION")
    print("=" * 80)

    # 1. Core Scripts
    print("\n📜 Core Scripts:")
    all_ok &= check_file(project_root / "run_pipeline.py", "Main pipeline orchestrator")
    all_ok &= check_file(project_root / "export_table.py", "Export utility")
    all_ok &= check_file(project_root / "reset_pipeline.py", "Reset utility")
    all_ok &= check_file(project_root / "clean_datalake.py", "Clean utility")
    all_ok &= check_file(project_root / "validate_setup.py", "This validation script")

    # 2. Configuration
    print("\n⚙️  Configuration:")
    all_ok &= check_file(project_root / "pyproject.toml", "Project configuration")
    all_ok &= check_file(project_root / "scrapy.cfg", "Scrapy configuration")

    # 3. Source Code
    print("\n🔧 Source Code:")
    all_ok &= check_file(project_root / "src/stage1/scout_spider.py", "Stage 1: Scout Spider")
    all_ok &= check_file(project_root / "src/stage2/stage2_worker.py", "Stage 2: Worker")
    all_ok &= check_file(project_root / "src/stage3/stage3_worker.py", "Stage 3: Worker")
    all_ok &= check_file(project_root / "src/common/delta_lake.py", "Delta Lake Manager")
    all_ok &= check_file(project_root / "src/stage2/intelligent_analyzer.py", "Intelligent Analyzer")

    # 4. Data Directories
    print("\n📁 Data Directories:")
    all_ok &= check_directory(project_root / "data", "Data root directory")
    all_ok &= check_directory(project_root / "data/raw", "Raw data directory")
    all_ok &= check_directory(project_root / "data/delta_lake", "Delta Lake directory")

    # 5. Seed File
    print("\n🌱 Seed File:")
    all_ok &= check_seed_file(project_root / "data/raw/uconn_urls.csv")

    # 6. Tests
    print("\n🧪 Tests:")
    all_ok &= check_file(project_root / "tests/integration/test_e2e_pipeline.py", "E2E integration test")
    all_ok &= check_file(project_root / "tests/test_stage2_worker.py", "Stage 2 tests")
    all_ok &= check_file(project_root / "tests/test_stage3_worker.py", "Stage 3 tests")

    # 7. Documentation
    print("\n📖 Documentation:")
    all_ok &= check_file(project_root / "README.md", "Main README")
    all_ok &= check_file(project_root / "PIPELINE_USAGE.md", "Usage Guide")
    all_ok &= check_file(project_root / "IMPROVEMENTS_SUMMARY.md", "Improvements Summary")

    # 8. Check Dependencies
    print("\n📦 Dependencies:")
    try:
        import scrapy
        print(f"  ✅ Scrapy: {scrapy.__version__}")
    except ImportError:
        print("  ❌ Scrapy not installed")
        all_ok = False

    try:
        import deltalake
        print(f"  ✅ Delta Lake installed")
    except ImportError:
        print("  ❌ Delta Lake not installed")
        all_ok = False

    try:
        import duckdb
        print(f"  ✅ DuckDB: {duckdb.__version__}")
    except ImportError:
        print("  ❌ DuckDB not installed")
        all_ok = False

    try:
        import httpx
        print(f"  ✅ HTTPX: {httpx.__version__}")
    except ImportError:
        print("  ❌ HTTPX not installed")
        all_ok = False

    # 9. Performance Settings Check
    print("\n⚡ Performance Settings:")
    try:
        from src.stage1.scout_spider import ScoutSpider
        settings = ScoutSpider.custom_settings
        print(f"  ✅ Spider Concurrent Requests: {settings['CONCURRENT_REQUESTS']}")
        print(f"  ✅ Spider Per-Domain Requests: {settings['CONCURRENT_REQUESTS_PER_DOMAIN']}")
        print(f"  ✅ Memory Limit: {settings['MEMUSAGE_LIMIT_MB']} MB")
    except Exception as e:
        print(f"  ⚠️  Could not load spider settings: {e}")

    # Final Status
    print("\n" + "=" * 80)
    if all_ok:
        print("✅ VALIDATION PASSED - Pipeline is ready to run!")
        print("=" * 80)
        print("\nNext Steps:")
        print("  1. Run pipeline: python run_pipeline.py")
        print("  2. Monitor: python export_table.py --list")
        print("  3. Export: python export_table.py --all")
        print("\nOr run integration test:")
        print("  pytest tests/integration/test_e2e_pipeline.py -v")
        return 0
    else:
        print("❌ VALIDATION FAILED - Please fix the issues above")
        print("=" * 80)
        print("\nInstall missing dependencies:")
        print("  pip install -e .")
        print("  pip install -e '.[dev]'  # For development tools")
        return 1


if __name__ == "__main__":
    sys.exit(main())
