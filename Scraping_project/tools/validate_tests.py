#!/usr/bin/env python3
"""Quick validation script to verify test infrastructure is ready"""

import sys
from pathlib import Path


def check_file_exists(file_path: str, description: str) -> bool:
    """Check if a file exists and report"""
    path = Path(file_path)
    exists = path.exists()
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {file_path}")
    return exists

def check_directory_exists(dir_path: str, description: str) -> bool:
    """Check if a directory exists and report"""
    path = Path(dir_path)
    exists = path.exists() and path.is_dir()
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {dir_path}")
    return exists

def validate_config_file(config_path: str) -> bool:
    """Validate configuration file can be loaded"""
    try:
        path = Path(config_path)
        if not path.exists():
            print(f"❌ Config file missing: {config_path}")
            return False

        import yaml
        with open(path, 'r') as f:
            config = yaml.safe_load(f)

        # Check for required sections
        required_sections = ['scrapy', 'stages', 'logging']
        missing_sections = [section for section in required_sections if section not in config]

        if missing_sections:
            print(f"❌ Missing config sections in {config_path}: {missing_sections}")
            return False

        print(f"✅ Config file valid: {config_path}")
        return True

    except Exception as e:
        print(f"❌ Config file error in {config_path}: {e}")
        return False

def check_data_availability() -> bool:
    """Check if test data is available"""
    uconn_csv = Path("data/raw/uconn_urls.csv")
    if uconn_csv.exists():
        try:
            with open(uconn_csv, 'r') as f:
                line_count = sum(1 for _ in f)
            print(f"✅ Test data available: {uconn_csv} ({line_count:,} lines)")
            return True
        except Exception as e:
            print(f"❌ Error reading test data: {e}")
            return False
    else:
        print(f"⚠️  Test data not found: {uconn_csv} (tests will use synthetic data)")
        return True  # Not critical, tests can use synthetic data

def validate_python_imports() -> bool:
    """Validate critical Python imports"""
    imports_to_check = [
        ('pytest', 'Testing framework'),
        ('scrapy', 'Web scraping framework'),
        ('aiohttp', 'Async HTTP client'),
        ('psutil', 'System monitoring'),
        ('yaml', 'Configuration parsing'),
        ('w3lib', 'URL utilities')
    ]

    all_good = True
    for module, description in imports_to_check:
        try:
            __import__(module)
            print(f"✅ {description}: {module}")
        except ImportError:
            print(f"❌ Missing dependency: {module} ({description})")
            all_good = False

    return all_good

def main():
    """Main validation function"""
    print("🔍 VALIDATING TEST INFRASTRUCTURE")
    print("=" * 50)

    all_checks = []

    # Check core test files
    print("\n📁 Core Test Files:")
    all_checks.append(check_file_exists("tests/conftest.py", "Test configuration"))
    all_checks.append(check_file_exists("pytest.ini", "Pytest configuration"))
    all_checks.append(check_file_exists("test_runner.py", "Standalone test runner"))
    all_checks.append(check_file_exists("run_tests.py", "Production test runner"))
    all_checks.append(check_file_exists("tests/test_config.py", "Test benchmarks"))

    # Check test directories
    print("\n📂 Test Directories:")
    all_checks.append(check_directory_exists("tests", "Main test directory"))
    all_checks.append(check_directory_exists("tests/unit", "Unit tests"))
    all_checks.append(check_directory_exists("tests/integration", "Integration tests"))

    # Check source code structure
    print("\n🏗️  Source Code Structure:")
    all_checks.append(check_directory_exists("src", "Source code directory"))
    all_checks.append(check_directory_exists("src/common", "Common utilities"))
    all_checks.append(check_directory_exists("src/stage1", "Stage 1 (Discovery)"))
    all_checks.append(check_directory_exists("src/stage2", "Stage 2 (Validation)"))
    all_checks.append(check_directory_exists("src/stage3", "Stage 3 (Enrichment)"))
    all_checks.append(check_directory_exists("src/orchestrator", "Pipeline orchestrator"))

    # Check configuration files
    print("\n⚙️  Configuration Files:")
    all_checks.append(check_directory_exists("config", "Config directory"))
    all_checks.append(validate_config_file("config/development.yml"))
    all_checks.append(validate_config_file("config/production.yml"))

    # Check data availability
    print("\n📊 Test Data:")
    all_checks.append(check_data_availability())

    # Check Python dependencies
    print("\n🐍 Python Dependencies:")
    all_checks.append(validate_python_imports())

    # Summary
    print("\n" + "=" * 50)
    print("📋 VALIDATION SUMMARY")
    print("=" * 50)

    passed_checks = sum(all_checks)
    total_checks = len(all_checks)
    success_rate = (passed_checks / total_checks) * 100

    print(f"✅ Passed: {passed_checks}/{total_checks} ({success_rate:.1f}%)")

    if success_rate >= 90:
        print("🎉 Test infrastructure is READY for production testing!")
        return 0
    elif success_rate >= 75:
        print("⚠️  Test infrastructure is mostly ready (minor issues)")
        return 0
    else:
        print("❌ Test infrastructure needs attention before testing")
        return 1

if __name__ == "__main__":
    sys.exit(main())