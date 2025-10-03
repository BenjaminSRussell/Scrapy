#!/usr/bin/env python3
"""
Data architecture consolidation script
Migrates legacy data/ and logs/ directories into the project structure
"""
import argparse
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def find_legacy_directories(project_root: Path) -> dict:
    """Find legacy data directories outside the project structure"""
    legacy_paths = {}

    # Check for data/ directories in parent directories
    for parent in [project_root.parent, project_root.parent.parent]:
        potential_data = parent / "data"
        potential_logs = parent / "logs"

        if potential_data.exists() and potential_data != project_root / "data":
            legacy_paths['data'] = potential_data
            logger.info(f"Found legacy data directory: {potential_data}")

        if potential_logs.exists() and potential_logs != project_root / "logs":
            legacy_paths['logs'] = potential_logs
            logger.info(f"Found legacy logs directory: {potential_logs}")

    return legacy_paths


def check_recent_activity(directory: Path, days: int = 7) -> bool:
    """Check if directory has recent activity"""
    cutoff_time = datetime.now().timestamp() - (days * 24 * 60 * 60)

    for file_path in directory.rglob("*"):
        if file_path.is_file():
            if file_path.stat().st_mtime > cutoff_time:
                return True

    return False


def migrate_directory(source: Path, target: Path, dry_run: bool = False) -> bool:
    """Migrate directory contents with conflict resolution"""
    logger.info(f"Migrating {source} -> {target}")

    if dry_run:
        logger.info("DRY RUN: Would migrate directory contents")
        return True

    try:
        # Create target directory if it doesn't exist
        target.mkdir(parents=True, exist_ok=True)

        # Copy contents
        for item in source.iterdir():
            target_item = target / item.name

            if item.is_file():
                if target_item.exists():
                    # Create backup with timestamp
                    backup_name = f"{item.name}.backup.{int(datetime.now().timestamp())}"
                    backup_path = target / backup_name
                    shutil.move(str(target_item), str(backup_path))
                    logger.warning(f"Backed up existing file: {backup_path}")

                shutil.copy2(str(item), str(target_item))
                logger.debug(f"Copied file: {item} -> {target_item}")

            elif item.is_dir():
                if target_item.exists():
                    # Merge directories recursively
                    migrate_directory(item, target_item, dry_run)
                else:
                    shutil.copytree(str(item), str(target_item))
                    logger.debug(f"Copied directory: {item} -> {target_item}")

        return True

    except Exception as e:
        logger.error(f"Failed to migrate {source}: {e}")
        return False


def update_config_files(project_root: Path, dry_run: bool = False):
    """Update configuration files to use consolidated paths"""
    config_dir = project_root / "config"
    if not config_dir.exists():
        logger.warning("Config directory not found, skipping config updates")
        return

    logger.info("Updating configuration files...")

    for config_file in config_dir.glob("*.yml"):
        if dry_run:
            logger.info(f"DRY RUN: Would update {config_file}")
            continue

        try:
            import yaml

            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)

            # Update data paths to use project-relative paths
            if 'data_paths' in config:
                paths = config['data_paths']
                if 'logs_dir' in paths:
                    paths['logs_dir'] = 'data/logs'
                if 'raw_dir' in paths:
                    paths['raw_dir'] = 'data/raw'
                if 'processed_dir' in paths:
                    paths['processed_dir'] = 'data/processed'

                logger.info(f"Updated data paths in {config_file}")

            # Create backup
            backup_file = config_file.with_suffix('.yml.backup')
            shutil.copy2(str(config_file), str(backup_file))

            # Write updated config
            with open(config_file, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)

        except Exception as e:
            logger.error(f"Failed to update {config_file}: {e}")


def create_migration_manifest(project_root: Path, migration_info: dict):
    """Create manifest of migration actions"""
    manifest_file = project_root / "data" / "migration_manifest.json"
    manifest_file.parent.mkdir(parents=True, exist_ok=True)

    import json

    manifest = {
        'migration_date': datetime.now().isoformat(),
        'migrated_directories': migration_info,
        'project_root': str(project_root),
        'consolidation_complete': True
    }

    with open(manifest_file, 'w') as f:
        json.dump(manifest, f, indent=2)

    logger.info(f"Migration manifest created: {manifest_file}")


def audit_current_paths(project_root: Path):
    """Audit current data directory structure"""
    logger.info("Auditing current data structure...")

    data_dir = project_root / "data"
    if data_dir.exists():
        logger.info(f"Project data directory: {data_dir}")
        for subdir in data_dir.iterdir():
            if subdir.is_dir():
                file_count = len(list(subdir.rglob("*")))
                logger.info(f"  {subdir.name}/: {file_count} files")
    else:
        logger.info("No project data directory found")

    logs_dir = project_root / "logs"
    if logs_dir.exists():
        file_count = len(list(logs_dir.rglob("*")))
        logger.info(f"Project logs directory: {logs_dir} ({file_count} files)")
    else:
        logger.info("No project logs directory found")


def main():
    parser = argparse.ArgumentParser(
        description="Consolidate data architecture under project directory"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be done without making changes'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Force migration even if no recent activity detected'
    )
    parser.add_argument(
        '--project-root',
        type=Path,
        default=Path(__file__).parent,
        help='Project root directory'
    )

    args = parser.parse_args()

    project_root = args.project_root.resolve()
    logger.info(f"Project root: {project_root}")

    # Audit current state
    audit_current_paths(project_root)

    # Find legacy directories
    legacy_paths = find_legacy_directories(project_root)

    if not legacy_paths:
        logger.info("✅ No legacy directories found - data architecture already consolidated")
        return 0

    # Check for recent activity
    migration_needed = False
    migration_info = {}

    for path_type, legacy_path in legacy_paths.items():
        if check_recent_activity(legacy_path) or args.force:
            logger.info(f"Recent activity detected in {legacy_path}")
            migration_needed = True
            migration_info[path_type] = {
                'source': str(legacy_path),
                'target': str(project_root / "data" / path_type),
                'files_count': len(list(legacy_path.rglob("*")))
            }
        else:
            logger.info(f"No recent activity in {legacy_path}")

    if not migration_needed and not args.force:
        logger.info("No migration needed - no recent activity in legacy directories")
        return 0

    if args.dry_run:
        logger.info("DRY RUN: Would perform the following migrations:")
        for path_type, info in migration_info.items():
            logger.info(f"  {info['source']} -> {info['target']} ({info['files_count']} files)")
        return 0

    # Perform migration
    logger.info("Starting data migration...")

    success = True
    for path_type, info in migration_info.items():
        source = Path(info['source'])
        target = Path(info['target'])

        if migrate_directory(source, target):
            logger.info(f"✅ Successfully migrated {path_type}")
        else:
            logger.error(f"❌ Failed to migrate {path_type}")
            success = False

    # Update configuration files
    update_config_files(project_root, dry_run=False)

    # Create migration manifest
    if success:
        create_migration_manifest(project_root, migration_info)
        logger.info("✅ Data architecture consolidation completed")
    else:
        logger.error("❌ Migration completed with errors")

    # Final audit
    audit_current_paths(project_root)

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())