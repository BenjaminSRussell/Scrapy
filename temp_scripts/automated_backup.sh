#!/bin/bash
# Automated Backup Script for Production Pipeline
set -e
BACKUP_DIR="${BACKUP_DIR:-./backups}"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS="${RETENTION_DAYS:-7}"
echo "🔄 Starting automated backup: $DATE"
mkdir -p "$BACKUP_DIR"
tar -czf "$BACKUP_DIR/delta_lake_$DATE.tar.gz" -C data delta_lake/ 2>/dev/null || echo "⚠️ Delta Lake backup skipped"
docker-compose exec -T postgres pg_dump -U postgres scraping_pipeline > "$BACKUP_DIR/postgres_$DATE.sql" 2>/dev/null || echo "⚠️ PostgreSQL backup skipped"
find "$BACKUP_DIR" -name "*.tar.gz" -mtime +$RETENTION_DAYS -delete
find "$BACKUP_DIR" -name "*.sql" -mtime +$RETENTION_DAYS -delete
echo "✅ Backup complete!"
ls -lh "$BACKUP_DIR" | tail -10
