#!/usr/bin/env python3
"""
Reorganize data structure to use single output directory.
Consolidates scattered data locations into unified structure.
"""

import shutil

from src.common.constants import (
    CACHE_DIR,
    DATA_DIR,
    LEGACY_SCRAPY_DATA,
    LOGS_DIR,
    OUTPUT_DIR,
    PROJECT_ROOT,
)


def reorganize_data_structure():
    """Reorganize data directories into standardized structure."""
    print("[*] Reorganizing data structure...")

    # 1. Merge temp and samples into output
    temp_dir = DATA_DIR / "temp"
    samples_dir = DATA_DIR / "samples"
    test_samples_dir = PROJECT_ROOT / "tests" / "samples"

    # Move temp files to output if any exist
    if temp_dir.exists() and any(temp_dir.iterdir()):
        print(f"  Moving temp files to {OUTPUT_DIR}/temp...")
        (OUTPUT_DIR / "temp").mkdir(parents=True, exist_ok=True)
        for item in temp_dir.iterdir():
            if item.is_file():
                shutil.move(str(item), str(OUTPUT_DIR / "temp" / item.name))

    # Keep test samples separate but consolidate data samples
    if samples_dir.exists() and samples_dir != test_samples_dir:
        if any(samples_dir.iterdir()):
            print(f"  Moving sample files to {OUTPUT_DIR}/samples...")
            (OUTPUT_DIR / "samples").mkdir(parents=True, exist_ok=True)
            for item in samples_dir.iterdir():
                if item.is_file():
                    shutil.move(str(item), str(OUTPUT_DIR / "samples" / item.name))

    # 2. Consolidate cache directories
    scrapy_cache = LEGACY_SCRAPY_DATA / "cache"
    DATA_DIR / "cache"

    if scrapy_cache.exists() and scrapy_cache != CACHE_DIR:
        print(f"  Merging {scrapy_cache} into {CACHE_DIR}...")
        if any(scrapy_cache.rglob("*")):
            for item in scrapy_cache.rglob("*"):
                if item.is_file():
                    rel_path = item.relative_to(scrapy_cache)
                    dest = CACHE_DIR / rel_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(item), str(dest))

    # 3. Move any output files to standardized locations
    legacy_outputs = [
        DATA_DIR / "raw",
        DATA_DIR / "processed",
        DATA_DIR / "exports",
    ]

    for legacy_dir in legacy_outputs:
        if legacy_dir.exists() and any(legacy_dir.iterdir()):
            target_name = legacy_dir.name
            print(f"  Moving {legacy_dir} to {OUTPUT_DIR}/{target_name}...")
            target_dir = OUTPUT_DIR / target_name
            target_dir.mkdir(parents=True, exist_ok=True)
            for item in legacy_dir.iterdir():
                if item.is_file():
                    shutil.move(str(item), str(target_dir / item.name))

    # 4. Clean up empty directories
    empty_dirs = [temp_dir, samples_dir]
    for empty_dir in empty_dirs:
        if empty_dir.exists() and not any(empty_dir.iterdir()):
            print(f"  Removing empty directory: {empty_dir}")
            empty_dir.rmdir()

    # 5. Create standardized stage output directories
    stage_dirs = ["stage1_discovery", "stage2_validation", "stage3_enrichment"]
    for stage_dir in stage_dirs:
        (OUTPUT_DIR / stage_dir).mkdir(parents=True, exist_ok=True)

    print("✅ Data structure reorganization complete!")
    print("\nNew structure:")
    print(f"  📁 {OUTPUT_DIR}/")
    print("     ├── stage1_discovery/    # Stage 1 outputs")
    print("     ├── stage2_validation/   # Stage 2 outputs")
    print("     ├── stage3_enrichment/   # Stage 3 outputs")
    print("     ├── temp/                # Temporary files")
    print("     └── samples/             # Sample data")
    print(f"  📁 {CACHE_DIR}/             # All cache data")
    print(f"  📁 {LOGS_DIR}/              # All log files")


def create_data_readme():
    """Create README explaining new data structure."""
    readme_content = """# Data Directory Structure

This directory contains all data for the UConn scraping pipeline.

## Directory Layout

```
data/
├── output/              # All pipeline outputs (SINGLE SOURCE OF TRUTH)
│   ├── stage1_discovery/    # Discovered URLs from Stage 1
│   ├── stage2_validation/   # Validated URLs from Stage 2
│   ├── stage3_enrichment/   # Enriched pages from Stage 3
│   ├── temp/                # Temporary working files
│   └── samples/             # Sample data for testing
│
├── cache/               # HTTP cache and intermediate results
│
├── logs/                # All log files
│   ├── pipeline.log         # Main pipeline log
│   ├── stage1.log           # Stage 1 specific
│   ├── stage2.log           # Stage 2 specific
│   └── stage3.log           # Stage 3 specific
│
├── checkpoints/         # Pipeline checkpoints for resume capability
│
├── config/              # Configuration files
│   ├── taxonomy.json        # Content taxonomy
│   └── uconn_glossary.json  # UConn-specific terms
│
└── warehouse/           # SQLite database (dev) or connection config (prod)

## Output File Naming Convention

All output files follow this pattern:
- Stage 1: `discovered_urls_YYYYMMDD_HHMMSS.jsonl`
- Stage 2: `validated_urls_YYYYMMDD_HHMMSS.jsonl`
- Stage 3: `enriched_pages_YYYYMMDD_HHMMSS.jsonl`

## Accessing Data Programmatically

Use the constants module for all paths:

```python
from src.common.constants import OUTPUT_DIR, LOGS_DIR, get_output_path

# Get stage-specific output path
output_path = get_output_path("stage3_enrichment", "enriched_pages.jsonl")

# Get log path
log_path = get_log_path("pipeline")
```

## Migration from Old Structure

The old structure with scattered data locations has been consolidated:
- `data/temp/` → `data/output/temp/`
- `data/samples/` → `data/output/samples/`
- `data/raw/` → `data/output/raw/`
- `data/processed/` → `data/output/processed/`
- `.scrapy/data/cache/` → `data/cache/`

## Cleanup

To remove old/duplicate directories:
```bash
python tools/reorganize_data_structure.py --cleanup
```
"""

    readme_path = DATA_DIR / "README.md"
    with open(readme_path, "w") as f:
        f.write(readme_content)

    print(f"✅ Created {readme_path}")


if __name__ == "__main__":
    import sys

    if "--cleanup" in sys.argv:
        # Additional cleanup of truly old/unnecessary files
        print("🧹 Running deep cleanup...")
        # Remove .scrapy directory if it exists
        if LEGACY_SCRAPY_DATA.exists():
            print(f"  Removing {LEGACY_SCRAPY_DATA}...")
            shutil.rmtree(LEGACY_SCRAPY_DATA, ignore_errors=True)

    reorganize_data_structure()
    create_data_readme()

    print("\n✨ All done! Data structure is now standardized.")
