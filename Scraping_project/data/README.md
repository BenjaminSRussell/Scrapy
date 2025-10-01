# Data Directory Structure

This directory contains all runtime data artifacts for the UConn Web Scraping Pipeline.

## Directory Overview

```
data/
├── raw/              # Input data and seed files
├── processed/        # Stage outputs (JSONL format)
├── cache/            # Persistent caches (SQLite databases)
├── exports/          # Exported data in various formats
├── logs/             # Application and pipeline logs
├── temp/             # Temporary files (auto-cleaned)
└── checkpoints/      # Pipeline state checkpoints
```

---

## Subdirectory Details

### 📥 `raw/` - Input Data
**Purpose:** Seed files and raw input data for the pipeline

**Contents:**
- `uconn_urls.csv` - Seed URLs for Stage 1 discovery
- Any other manually curated input files

**Format:** CSV files with one URL per line (no header)

**Example:**
```csv
https://uconn.edu
https://catalog.uconn.edu
https://registrar.uconn.edu
```

---

### 📤 `processed/` - Pipeline Outputs
**Purpose:** Processed data from each pipeline stage

**Structure:**
```
processed/
├── stage01/          # Discovery spider outputs
│   └── discovery_output.jsonl
├── stage02/          # URL validation outputs
│   └── validation_output.jsonl
└── stage03/          # Content enrichment outputs
    └── enriched_data.jsonl
```

**Stage 1 Output Schema:**
```json
{
  "source_url": "https://uconn.edu",
  "discovered_url": "https://uconn.edu/academics",
  "first_seen": "2025-10-01T12:00:00",
  "discovery_depth": 1,
  "discovery_source": "html_link",
  "confidence": 1.0
}
```

**Stage 2 Output Schema:**
```json
{
  "url": "https://uconn.edu/academics",
  "url_hash": "abc123...",
  "status_code": 200,
  "is_valid": true,
  "content_type": "text/html",
  "response_time": 0.234,
  "validated_at": "2025-10-01T12:01:00"
}
```

**Stage 3 Output Schema:**
```json
{
  "url": "https://uconn.edu/academics",
  "title": "Academics - University of Connecticut",
  "text_content": "...",
  "word_count": 1234,
  "entities": ["University of Connecticut", "STEM"],
  "keywords": ["academics", "programs", "degrees"],
  "enriched_at": "2025-10-01T12:02:00"
}
```

---

### 💾 `cache/` - Persistent Caches
**Purpose:** SQLite databases for persistent storage and deduplication

**Contents:**
- `url_cache.db` - URL deduplication cache (Stage 1)
  - Stores all discovered URLs with hashes
  - Enables O(1) deduplication
  - Persists across pipeline restarts
  - Used for resume capability

**Benefits:**
- **Memory Efficient:** O(1) memory usage instead of O(n)
- **Fast Restarts:** Instant resume without re-reading JSONL
- **Scalable:** Handles millions of URLs
- **Persistent:** Survives crashes and restarts

**Schema:**
```sql
CREATE TABLE urls (
    url_hash TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    discovered_at TEXT,
    validated_at TEXT,
    enriched_at TEXT,
    status_code INTEGER,
    is_valid BOOLEAN,
    content_type TEXT,
    title TEXT,
    word_count INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**Management:**
```bash
# View cache stats
sqlite3 data/cache/url_cache.db "SELECT COUNT(*) FROM urls;"

# Clear cache (for fresh crawl)
rm -f data/cache/url_cache.db

# Inspect recent entries
sqlite3 data/cache/url_cache.db "SELECT url, discovered_at FROM urls ORDER BY discovered_at DESC LIMIT 10;"
```

---

### 📊 `exports/` - Data Exports
**Purpose:** Exported data in various formats for downstream use

**Supported Formats:**
- CSV files for spreadsheet analysis
- JSON for API consumption
- Database exports for SQL analysis
- XML for legacy systems

**Naming Convention:**
```
{stage}_{format}_{timestamp}.{ext}
```

**Examples:**
```
exports/
├── stage03_csv_20251001_120000.csv
├── stage03_json_20251001_120000.json
└── enriched_data_export.db
```

---

### 📝 `logs/` - Application Logs
**Purpose:** Application logs and pipeline execution logs

**Structure:**
```
logs/
├── pipeline.log          # Main pipeline log (rotating)
├── discovery.log         # Stage 1 specific logs
├── validation.log        # Stage 2 specific logs
├── enrichment.log        # Stage 3 specific logs
└── alerts.jsonl          # Alert events (when alerting enabled)
```

**Log Rotation:**
- Files rotate at 10MB by default
- Keep 5 backup files
- Configured in `config/{env}.yml`

**Log Formats:**
- **Structured JSON:** When `structured: true` in config
- **Plain Text:** Default format for human readability

**Example JSON Log:**
```json
{
  "timestamp": "2025-10-01T12:00:00.123Z",
  "level": "INFO",
  "logger": "discovery_spider",
  "message": "Discovered 150 URLs at depth 2",
  "extra": {
    "depth": 2,
    "url_count": 150
  }
}
```

---

### 🗂️ `temp/` - Temporary Files
**Purpose:** Temporary files created during pipeline execution

**Contents:**
- Intermediate processing files
- Temporary download artifacts
- Queue state snapshots
- Enrichment URL lists

**Cleanup:**
- Automatically cleaned on pipeline completion
- Manual cleanup: `make clean-temp` or `rm -rf data/temp/*`
- Old files removed based on age (default: 24 hours)

**Examples:**
```
temp/
├── enrichment_urls_20251001_120000.json
└── stage2_batch_001.tmp
```

---

### 🔄 `checkpoints/` - Pipeline State
**Purpose:** Checkpoint files for resumable operations

**Contents:**
- `stage01.checkpoint.json` - Stage 1 progress
- `stage02.checkpoint.json` - Stage 2 progress
- `stage03.checkpoint.json` - Stage 3 progress

**Checkpoint Schema:**
```json
{
  "stage": "discovery",
  "batch_id": 5,
  "last_processed_line": 10000,
  "total_processed": 50000,
  "status": "processing",
  "created_at": "2025-10-01T12:00:00",
  "updated_at": "2025-10-01T12:05:00",
  "metadata": {
    "input_file_hash": "abc123..."
  }
}
```

**Resume Capability:**
- Checkpoints saved every 100 items processed
- Validates checkpoint freshness (default: 24 hours)
- Detects input file changes via SHA256 hash
- Supports mid-batch resume

**Management:**
```bash
# View checkpoint status
cat data/checkpoints/stage01.checkpoint.json

# Reset checkpoint (for fresh start)
make reset-checkpoints
# or
rm -f data/checkpoints/*.checkpoint.json
```

---

## Data Lifecycle

```
┌─────────────┐
│  raw/       │  Seed URLs (manual input)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Stage 1    │  Discovery Spider
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ processed/  │  discovery_output.jsonl
│ stage01/    │  (URLs with metadata)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Stage 2    │  URL Validator
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ processed/  │  validation_output.jsonl
│ stage02/    │  (Valid URLs only)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  Stage 3    │  Content Enricher
└──────┬──────┘
       │
       ▼
┌─────────────┐
│ processed/  │  enriched_data.jsonl
│ stage03/    │  (Full text + NLP)
└──────┬──────┘
       │
       ▼
┌─────────────┐
│  exports/   │  Various formats for
└─────────────┘  downstream consumption
```

---

## Storage Optimization

### Disk Space Management

**Compression:**
- JSONL files can be gzipped to save space
- Example: `gzip data/processed/stage01/discovery_output.jsonl`
- Pipeline can read `.jsonl.gz` files directly

**Cleanup Commands:**
```bash
# Clear all processed data (keep raw and cache)
make clean-data

# Clear only temp files
make clean-temp

# Clear everything including cache (fresh start)
make clean-all

# Clear old checkpoints (>7 days)
find data/checkpoints -mtime +7 -delete
```

### Database Maintenance

**SQLite Optimization:**
```bash
# Vacuum database (reclaim space)
sqlite3 data/cache/url_cache.db "VACUUM;"

# Analyze for query optimization
sqlite3 data/cache/url_cache.db "ANALYZE;"

# Check database integrity
sqlite3 data/cache/url_cache.db "PRAGMA integrity_check;"
```

---

## Backup Recommendations

### Critical Data
- ✅ `raw/` - **Always backup** (source of truth)
- ✅ `cache/url_cache.db` - **Backup for large crawls** (avoid re-discovery)
- ⚠️ `processed/` - **Optional** (can be regenerated)
- ❌ `temp/`, `logs/` - **No backup needed** (ephemeral)

### Backup Strategy
```bash
# Backup critical data
tar -czf backup_$(date +%Y%m%d).tar.gz data/raw/ data/cache/

# Backup processed outputs
tar -czf processed_backup_$(date +%Y%m%d).tar.gz data/processed/

# Restore from backup
tar -xzf backup_20251001.tar.gz
```

---

## File Size Estimates

Approximate sizes for a typical crawl:

| Directory | Size (10K URLs) | Size (100K URLs) | Size (1M URLs) |
|-----------|-----------------|------------------|----------------|
| `raw/` | < 1 MB | < 10 MB | < 100 MB |
| `processed/stage01/` | 5 MB | 50 MB | 500 MB |
| `processed/stage02/` | 3 MB | 30 MB | 300 MB |
| `processed/stage03/` | 50 MB | 500 MB | 5 GB |
| `cache/url_cache.db` | 2 MB | 20 MB | 200 MB |
| `logs/` | 10 MB | 50 MB | 100 MB |

**Total Estimate:** 70 MB (10K URLs), 660 MB (100K URLs), 6.2 GB (1M URLs)

---

## Configuration

The data directory paths are configurable in `config/{env}.yml`:

```yaml
data:
  raw_dir: "data/raw"
  processed_dir: "data/processed"
  cache_dir: "data/cache"
  exports_dir: "data/exports"
  logs_dir: "data/logs"
  temp_dir: "data/temp"
  checkpoints_dir: "data/checkpoints"
```

---

## Troubleshooting

### "Disk space full" errors
```bash
# Check disk usage
du -sh data/*

# Clear temporary files
rm -rf data/temp/*

# Compress old logs
gzip data/logs/*.log

# Remove old processed data
rm -f data/processed/stage*/*.jsonl.old
```

### "Database locked" errors
```bash
# Check for stale locks
ls -la data/cache/*.db-wal

# Kill orphaned processes
pkill -f "scrapy crawl"

# Reset database (last resort)
rm -f data/cache/url_cache.db-wal data/cache/url_cache.db-shm
```

### Corrupt checkpoint files
```bash
# Validate checkpoint JSON
jq . data/checkpoints/stage01.checkpoint.json

# Reset if corrupt
rm -f data/checkpoints/stage01.checkpoint.json
```

---

## Best Practices

1. **Regular Backups:** Backup `raw/` and `cache/` before major changes
2. **Monitor Disk Space:** Keep 20% free for SQLite WAL files
3. **Clean Temp Files:** Run cleanup after each pipeline execution
4. **Version Control:** **Never** commit `data/` to git (use .gitignore)
5. **Log Rotation:** Enable rotation to prevent log files from growing unbounded
6. **Checkpoint Cleanup:** Remove stale checkpoints older than 7 days

---

**Last Updated:** 2025-10-01
**Maintained By:** Pipeline Team
**Questions?** See [docs/architecture.md](../docs/architecture.md) or [docs/development.md](../docs/development.md)
