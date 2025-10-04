# Data Directory Structure

Pipeline data inputs, outputs, and cache files.

---

## Directory Layout

```
data/
├── raw/                    # Input seed files
│   └── uconn_urls.csv     # Seed URLs for discovery
├── processed/             # Pipeline outputs
│   ├── stage01/          # Discovery outputs (intermediate)
│   ├── stage02/          # Validation outputs (intermediate)
│   └── stage03/          # FINAL enriched outputs
├── cache/                 # URL deduplication databases
├── checkpoints/           # Pipeline resume points
└── logs/                  # Application logs
```

---

## Important: Only Stage 3 Output Matters

**Final output location**: `processed/stage03/enriched_content.jsonl`

- Stage 1 and Stage 2 outputs are intermediate files
- Stage 3 contains fully enriched content with NLP
- All other directories are cache/temp files

---

## Output Files

### Stage 1: Discovery
**File**: `processed/stage01/discovery_output.jsonl`
- Discovered URLs from seed crawling
- Intermediate data, not final output

### Stage 2: Validation
**File**: `processed/stage02/validation_output.jsonl`
- Validated URLs with HTTP checks
- Intermediate data, not final output

### Stage 3: Enrichment (FINAL)
**File**: `processed/stage03/enriched_content.jsonl`
- **This is the final output**
- Contains NLP-enriched content
- Full text, entities, keywords, categories

---

## Deduplication Cache

**Location**: `cache/`

Contains SQLite databases for URL deduplication:
- `stage1_dedup.db` - Discovery deduplication
- `stage2_dedup.db` - Validation deduplication
- `stage3_dedup.db` - Enrichment deduplication

Safe to delete - will be regenerated on next run.

---

## Checkpoints

**Location**: `checkpoints/`

Resume points for pipeline stages:
- Allows restarting from last successful checkpoint
- Automatically created during pipeline execution
- Safe to delete if you want a fresh start

---

## Logs

**Location**: `logs/`

Application logs with structured JSON output:
- `pipeline_YYYYMMDD.log` - Daily pipeline logs
- `scrapy_YYYYMMDD.log` - Scrapy-specific logs

Old logs can be archived or deleted.

---

## Cleanup Guide

### Safe to Delete
- `cache/` - Regenerated automatically
- `checkpoints/` - Recreated on next run
- `logs/` - Old logs can be archived
- `processed/stage01/` - Intermediate files
- `processed/stage02/` - Intermediate files

### Never Delete
- `raw/uconn_urls.csv` - Required seed file
- `processed/stage03/` - **Final output**

---

## Disk Usage

Typical sizes for 100K URLs:
- `processed/stage01/` - ~50MB
- `processed/stage02/` - ~100MB
- `processed/stage03/` - ~500MB
- `cache/` - ~50MB
- `checkpoints/` - ~10MB
- `logs/` - ~100MB

**Total**: ~810MB for complete pipeline run

---

**Last Updated**: October 4, 2025
