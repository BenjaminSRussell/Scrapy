#!/usr/bin/env python3
"""
Phase 1: File Count Audit
Scan all directories and count Python files to identify directories with >4 files.
"""

from pathlib import Path
from collections import defaultdict

def audit_file_counts(root_dir: Path):
    """Audit file counts in all directories."""

    results = []

    for directory in sorted(root_dir.rglob('*')):
        if not directory.is_dir():
            continue

        if any(part.startswith('.') for part in directory.parts):
            continue

        if '__pycache__' in str(directory):
            continue

        py_files = list(directory.glob('*.py'))

        if len(py_files) > 0:
            status = "⚠️  OVER LIMIT" if len(py_files) > 4 else "✅ OK"
            results.append((directory, len(py_files), status))

    return results

def main():
    print("=" * 80)
    print("PHASE 1: FILE COUNT AUDIT")
    print("=" * 80)
    print()
    print("Goal: No directory should have more than 4 Python files")
    print()

    src_dir = Path("src")

    if not src_dir.exists():
        print(f"❌ Error: {src_dir} not found")
        return

    results = audit_file_counts(src_dir)

    print(f"{'Directory':<50} {'Files':<10} {'Status'}")
    print("-" * 80)

    over_limit = []

    for directory, count, status in results:
        print(f"{str(directory):<50} {count:<10} {status}")
        if count > 4:
            over_limit.append((directory, count))

    print()
    print("=" * 80)
    print(f"SUMMARY")
    print("=" * 80)
    print(f"Total directories scanned: {len(results)}")
    print(f"Directories over limit (>4 files): {len(over_limit)}")
    print()

    if over_limit:
        print("🔴 DIRECTORIES REQUIRING REORGANIZATION:")
        for directory, count in over_limit:
            print(f"   {directory}: {count} files (need to reduce by {count - 4})")
    else:
        print("✅ All directories are within the limit!")

    return over_limit

if __name__ == "__main__":
    main()
