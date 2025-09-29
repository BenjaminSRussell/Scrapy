#!/usr/bin/env python3
"""Simple test runner for the UConn scraping pipeline."""

import subprocess
import sys
from pathlib import Path


def run_tests(test_type="all"):
    """Run tests based on type specified."""
    project_root = Path(__file__).parent.parent

    if test_type == "smoke":
        # Run just a few quick tests
        cmd = ["python", "-m", "pytest", "tests/common/test_logging.py", "-v"]
    elif test_type == "unit":
        # Run unit tests only
        cmd = ["python", "-m", "pytest", "tests/", "-k", "not integration", "-v"]
    elif test_type == "integration":
        # Run integration tests only
        cmd = ["python", "-m", "pytest", "tests/integration/", "-v"]
    else:
        # Run all tests
        cmd = ["python", "-m", "pytest", "tests/", "-v"]

    print(f"Running tests: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=project_root, check=False)
        return result.returncode
    except FileNotFoundError:
        print("Error: pytest not found. Install with: pip install pytest")
        return 1


if __name__ == "__main__":
    test_type = sys.argv[1] if len(sys.argv) > 1 else "all"
    exit_code = run_tests(test_type)
    sys.exit(exit_code)