# Deprecated Scripts

These scripts have been **consolidated** into the unified CLI at `run_pipeline.py`.

## Migration Guide

### Old → New Command Mapping

| Old Script | New Unified CLI Command |
|-----------|------------------------|
| `python setup_models.py` | `python run_pipeline.py setup` |
| `python clean_datalake.py` | `python run_pipeline.py clean` |
| `python reset_pipeline.py` | `python run_pipeline.py reset` |
| `python export_table.py --list` | `python run_pipeline.py export --list` |
| `python export_table.py --all` | `python run_pipeline.py export --all` |
| `python validate_setup.py` | `python run_pipeline.py validate` |

## Why Were These Deprecated?

These standalone scripts were consolidated to:
- **Single Entry Point**: One command (`run_pipeline.py`) for all operations
- **Better UX**: Consistent CLI interface with subcommands
- **Reduced Duplication**: Shared code moved to `src/common/delta_lake.py`
- **Easier Maintenance**: Changes only needed in one place

## Files in This Directory

- `setup_models.py` - Model download (now: `run_pipeline.py setup`)
- `clean_datalake.py` - Delta Lake cleanup (now: `run_pipeline.py clean`)
- `reset_pipeline.py` - Pipeline reset (now: `run_pipeline.py reset`)
- `export_table.py` - Data export (now: `run_pipeline.py export`)
- `validate_setup.py` - Setup validation (now: `run_pipeline.py validate`)

These files are kept for reference only and should not be used.

## New Unified CLI

```bash
# View all available commands
python run_pipeline.py --help

# Run pipeline
python run_pipeline.py run

# Setup models
python run_pipeline.py setup

# Validate installation
python run_pipeline.py validate

# Check health
python run_pipeline.py health

# Export data
python run_pipeline.py export --list
python run_pipeline.py export --all

# Clean/Reset
python run_pipeline.py clean
python run_pipeline.py reset
```

See main [README.md](../README.md) for complete documentation.
