# Data Warehouse Guide

## Overview

The UConn Web Scraping Pipeline now includes a **comprehensive data warehouse** architecture that transforms raw scraped data into a production-ready relational database. This guide covers the complete warehouse system, from schema design to query patterns.

---

## Table of Contents

- [Architecture](#architecture)
- [Database Schema](#database-schema)
- [Getting Started](#getting-started)
- [Python Warehouse Integration](#python-warehouse-integration)
- [PostgreSQL Setup](#postgresql-setup)
- [Versioning & Change Tracking](#versioning--change-tracking)
- [Vendor Data Integration](#vendor-data-integration)
- [Query Patterns](#query-patterns)
- [Java ETL Loader](#java-etl-loader)
- [Performance Optimization](#performance-optimization)
- [Migration Guide](#migration-guide)

---

## Architecture

### Data Flow

```
Python Scraping Pipeline
         ↓
    JSONL Output (Raw)
         ↓
┌────────────────────────┐
│  Python Warehouse API  │
│  (Quick Development)   │
└────────────────────────┘
         ↓
    SQLite/PostgreSQL
         ↓
┌────────────────────────┐
│   Java ETL Loader      │
│ (Production Scale)     │
└────────────────────────┘

**IMPORTANT**: The Python pipeline is for **local development and prototyping only**. The Java ETL loader is the **only** supported method for production data loading.
         ↓
  Production PostgreSQL
  Data Warehouse
```

### Two-Tier Approach

**Tier 1: Python Direct Load (Development Only)**
- Fast iteration
- Immediate results
- SQLite or PostgreSQL
- Good for <100k records

**Tier 2: Java ETL (Production Only)**
- Enterprise-grade transformations
- Optimized bulk loading
- Advanced deduplication
- Good for millions of records
- Enterprise-grade transformations
- Optimized bulk loading
- Advanced deduplication
- Good for millions of records

---

## Database Schema

### Core Tables

#### 1. Pages (Fact Table)

**Purpose**: Core content with versioning

```sql
CREATE TABLE pages (
    page_id SERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL,
    title TEXT,
    text_content TEXT,
    word_count INTEGER DEFAULT 0,
    content_type TEXT,
    status_code INTEGER,
    has_pdf_links BOOLEAN DEFAULT FALSE,
    has_audio_links BOOLEAN DEFAULT FALSE,

    -- Versioning
    first_seen_at TIMESTAMP NOT NULL,
    last_crawled_at TIMESTAMP NOT NULL,
    crawl_version INTEGER DEFAULT 1,
    is_current BOOLEAN DEFAULT TRUE,

    -- Audit
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(url_hash, crawl_version)
);
```

**Key Fields**:
- `url_hash`: SHA-256 hash for deduplication
- `crawl_version`: Incremented on each crawl
- `is_current`: TRUE only for latest version
- `first_seen_at`: Never changes
- `last_crawled_at`: Updated on each crawl

#### 2. Entities (Dimension Table)

**Purpose**: Named entities extracted by NLP

```sql
CREATE TABLE entities (
    entity_id SERIAL PRIMARY KEY,
    page_id INTEGER REFERENCES pages(page_id),
    entity_text TEXT NOT NULL,
    entity_type TEXT,
    confidence REAL DEFAULT 1.0,
    source TEXT DEFAULT 'spacy',  -- spacy, transformer, glossary

    crawl_version INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 3. Keywords (Dimension Table)

**Purpose**: Keywords/terms from content

```sql
CREATE TABLE keywords (
    keyword_id SERIAL PRIMARY KEY,
    page_id INTEGER REFERENCES pages(page_id),
    keyword_text TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    relevance_score REAL DEFAULT 1.0,
    source TEXT DEFAULT 'nlp',  -- nlp, glossary, manual

    crawl_version INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 4. Categories (Dimension Table)

**Purpose**: Taxonomy classifications

```sql
CREATE TABLE categories (
    category_id SERIAL PRIMARY KEY,
    page_id INTEGER REFERENCES pages(page_id),
    category_name TEXT NOT NULL,
    category_path TEXT,  -- hierarchical: healthcare.medical_education
    confidence_score REAL DEFAULT 1.0,
    matched_keywords JSONB,  -- Keywords that triggered this category

    crawl_version INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 5. Crawl History (Audit Table)

**Purpose**: Track crawl sessions

```sql
CREATE TABLE crawl_history (
    crawl_id SERIAL PRIMARY KEY,
    crawl_timestamp TIMESTAMP NOT NULL,
    stage TEXT NOT NULL,  -- discovery, validation, enrichment
    pages_processed INTEGER DEFAULT 0,
    pages_successful INTEGER DEFAULT 0,
    pages_failed INTEGER DEFAULT 0,
    duration_seconds REAL,
    status TEXT DEFAULT 'running',

    config_snapshot JSONB,  -- Configuration used
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
```

#### 6. Vendor Data (Integration Table)

**Purpose**: Third-party data sources

```sql
CREATE TABLE vendor_data (
    vendor_id SERIAL PRIMARY KEY,
    page_id INTEGER REFERENCES pages(page_id),
    vendor_name TEXT NOT NULL,
    vendor_url TEXT,
    data_type TEXT NOT NULL,  -- api, extract, manual
    raw_data JSONB,

    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

#### 7. Page Changes (History Table)

**Purpose**: Track content changes over time

```sql
CREATE TABLE page_changes (
    change_id SERIAL PRIMARY KEY,
    page_id INTEGER REFERENCES pages(page_id),
    previous_version INTEGER,
    current_version INTEGER NOT NULL,
    change_type TEXT NOT NULL,  -- title, content, metadata
    old_value TEXT,
    new_value TEXT,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Getting Started

### 1. Install Dependencies

```bash
# PostgreSQL (optional, but recommended for production)
pip install psycopg2-binary

# Already included in requirements.txt
```

### 2. Initialize Warehouse

**Python Code**:
```python
from src.common.warehouse import DataWarehouse
from src.common.warehouse_schema import DatabaseType

# SQLite (development)
warehouse = DataWarehouse(
    db_type=DatabaseType.SQLITE,
    connection_string="data/warehouse/uconn_warehouse.db"
)

# PostgreSQL (production)
warehouse = DataWarehouse(
    db_type=DatabaseType.POSTGRESQL,
    connection_string="postgresql://user:password@localhost:5432/uconn_warehouse"
)
```

### 3. Load Data

**From Enrichment Spider**:

Enable warehouse pipeline in Scrapy settings:

```python
# Add to ITEM_PIPELINES in settings.py
ITEM_PIPELINES = {
    'src.stage3.warehouse_pipeline.DataWarehousePipeline': 400,
}

# Configure warehouse
WAREHOUSE_ENABLED = True
WAREHOUSE_DB_TYPE = 'sqlite'  # or 'postgresql'
WAREHOUSE_CONNECTION_STRING = 'data/warehouse/uconn_warehouse.db'
WAREHOUSE_CRAWL_VERSION = 1  # Increment each crawl
```

Run enrichment:
```bash
python main.py --env development --stage 3
```

---

## Python Warehouse Integration

> **⚠️ For Development & Prototyping Only**
> This method provides a quick way to load data for local testing. It is **not** intended for production use. For production, use the [Java ETL Loader](#java-etl-loader).

### Writing Data

**Insert Page with Related Data**:
```python
from src.common.warehouse import DataWarehouse
from src.common.warehouse_schema import PageRecord, EntityRecord, KeywordRecord, CategoryRecord
from datetime import datetime

warehouse = DataWarehouse(db_type=DatabaseType.SQLITE)

# Create page
page = PageRecord(
    url="https://uconn.edu/academics/",
    url_hash="abc123...",
    title="Academics | UConn",
    text_content="...",
    word_count=500,
    content_type="text/html",
    status_code=200,
    crawl_version=1
)

page_id = warehouse.insert_page(page)

# Add entities
entities = [
    EntityRecord(page_id=page_id, entity_text="UConn", crawl_version=1),
    EntityRecord(page_id=page_id, entity_text="College of Liberal Arts", crawl_version=1)
]
warehouse.insert_entities(entities)

# Add keywords
keywords = [
    KeywordRecord(page_id=page_id, keyword_text="undergraduate", crawl_version=1),
    KeywordRecord(page_id=page_id, keyword_text="graduate", crawl_version=1)
]
warehouse.insert_keywords(keywords)

# Add categories
categories = [
    CategoryRecord(
        page_id=page_id,
        category_name="Academics",
        category_path="academics.undergraduate",
        crawl_version=1
    )
]
warehouse.insert_categories(categories)
```

### Reading Data

**Get Current Pages**:
```python
for page in warehouse.get_current_pages(limit=100):
    print(f"{page['title']}: {page['url']}")
```

**Get Page with Details**:
```python
page_details = warehouse.get_page_with_details(page_id=1)
print(f"Title: {page_details['title']}")
print(f"Entities: {page_details['entities']}")
print(f"Keywords: {page_details['keywords']}")
print(f"Categories: {page_details['categories']}")
```

---

## PostgreSQL Setup

### 1. Install PostgreSQL

**Ubuntu/Debian**:
```bash
sudo apt update
sudo apt install postgresql postgresql-contrib
```

**macOS**:
```bash
brew install postgresql
brew services start postgresql
```

**Windows**: Download from [postgresql.org](https://www.postgresql.org/download/windows/)

### 2. Create Database

```bash
# Connect to PostgreSQL
sudo -u postgres psql

# Create database and user
CREATE DATABASE uconn_warehouse;
CREATE USER warehouse_user WITH ENCRYPTED PASSWORD 'your-password';
GRANT ALL PRIVILEGES ON DATABASE uconn_warehouse TO warehouse_user;

# Exit
\q
```

### 3. Initialize Schema

**Python**:
```python
from src.common.warehouse import DataWarehouse
from src.common.warehouse_schema import DatabaseType

warehouse = DataWarehouse(
    db_type=DatabaseType.POSTGRESQL,
    connection_string="postgresql://warehouse_user:your-password@localhost:5432/uconn_warehouse"
)

# Schema automatically created on first connection
```

**Or manually**:
```bash
# Get schema SQL
python -c "from src.common.warehouse_schema import get_schema_sql, DatabaseType; print(get_schema_sql(DatabaseType.POSTGRESQL))" > schema.sql

# Apply to database
psql -U warehouse_user -d uconn_warehouse -f schema.sql
```

---

## Versioning & Change Tracking

### How Versioning Works

1. **First Crawl** (version 1):
   - Insert new page with `crawl_version=1`, `is_current=TRUE`

2. **Subsequent Crawls** (version 2, 3, ...):
   - Find existing page by `url_hash`
   - Mark old version as `is_current=FALSE`
   - Insert new version with incremented `crawl_version`
   - Record changes in `page_changes` table

### Example: Tracking Title Change

**Crawl 1** (Oct 1):
```
page_id=1, url_hash=abc123, title="Old Title", crawl_version=1, is_current=TRUE
```

**Crawl 2** (Oct 8):
```
page_id=1, url_hash=abc123, title="Old Title", crawl_version=1, is_current=FALSE
page_id=5, url_hash=abc123, title="New Title", crawl_version=2, is_current=TRUE

page_changes:
change_id=1, page_id=5, change_type="title", old_value="Old Title", new_value="New Title"
```

### Querying Historical Data

**Get all versions of a page**:
```sql
SELECT crawl_version, title, last_crawled_at
FROM pages
WHERE url_hash = 'abc123...'
ORDER BY crawl_version DESC;
```

**Get pages changed in last 7 days**:
```sql
SELECT p.url, p.title, pc.change_type, pc.changed_at
FROM pages p
JOIN page_changes pc ON p.page_id = pc.page_id
WHERE pc.changed_at > NOW() - INTERVAL '7 days';
```

---

## Vendor Data Integration

### Purpose

Integrate data from sources **not accessible via web crawling**:
- Internal APIs (People Directory, Course Catalog)
- Manual data imports (CSV, Excel from departments)
- External databases (HR system, Student Information System)
- Document repositories (SharePoint, Google Drive)

### Configuration

**Create vendor config**: `data/config/vendor_config.json`

```json
{
  "vendors": [
    {
      "name": "UConn People Directory API",
      "type": "api",
      "url": "https://api.uconn.edu/people",
      "credentials": {
        "api_key": "your-api-key"
      },
      "enabled": true
    },
    {
      "name": "Course Catalog Extract",
      "type": "file",
      "url": "data/vendor/course_catalog.json",
      "enabled": true
    },
    {
      "name": "Student System Database",
      "type": "database",
      "url": "postgresql://localhost/student_db",
      "credentials": {
        "db_type": "postgresql",
        "query": "SELECT * FROM courses WHERE active = TRUE"
      },
      "enabled": false
    }
  ]
}
```

### Running Vendor Integration

```python
from src.common.vendor_integration import VendorIntegrationManager
from src.common.warehouse import DataWarehouse

warehouse = DataWarehouse()
manager = VendorIntegrationManager(warehouse)

# Load vendor configurations
manager.load_vendor_config("data/config/vendor_config.json")

# Extract and load all vendor data
results = manager.extract_all()
print(results)  # {'UConn People Directory API': 1500, 'Course Catalog Extract': 3000}
```

---

## Query Patterns

### Common Queries

**1. Get all current pages with categories**
```sql
SELECT p.url, p.title, c.category_name
FROM pages p
LEFT JOIN categories c ON p.page_id = c.page_id
WHERE p.is_current = TRUE
ORDER BY p.last_crawled_at DESC;
```

**2. Find pages by keyword**
```sql
SELECT DISTINCT p.url, p.title
FROM pages p
JOIN keywords k ON p.page_id = k.page_id
WHERE k.keyword_text = 'research'
  AND p.is_current = TRUE;
```

**3. Get most common entities**
```sql
SELECT entity_text, COUNT(*) as frequency
FROM entities e
JOIN pages p ON e.page_id = p.page_id
WHERE p.is_current = TRUE
GROUP BY entity_text
ORDER BY frequency DESC
LIMIT 20;
```

**4. Category distribution**
```sql
SELECT category_name, COUNT(*) as page_count
FROM categories c
JOIN pages p ON c.page_id = p.page_id
WHERE p.is_current = TRUE
GROUP BY category_name
ORDER BY page_count DESC;
```

**5. Content staleness report**
```sql
SELECT url, title, last_crawled_at,
       NOW() - last_crawled_at AS age
FROM pages
WHERE is_current = TRUE
  AND last_crawled_at < NOW() - INTERVAL '30 days'
ORDER BY age DESC;
```

**6. Vendor data enrichment**
```sql
SELECT p.url, p.title, v.vendor_name, v.raw_data
FROM pages p
LEFT JOIN vendor_data v ON p.page_id = v.page_id
WHERE v.vendor_name = 'UConn People Directory API'
  AND p.is_current = TRUE;
```

---

## Java ETL Loader

For production deployments with millions of records, use the **Java Data Warehouse Loader**.

**Benefits**:
- 10x faster bulk loading
- Enterprise-grade error handling
- Advanced deduplication algorithms
- Spring Batch for scalability
- Integration with data pipelines (Airflow, NiFi)

**See**: [java_warehouse_loader.md](java_warehouse_loader.md) for complete specification

---

## Performance Optimization

### Indexing Strategy

**Create indexes** (already in schema):
```sql
CREATE INDEX idx_pages_url_hash ON pages(url_hash);
CREATE INDEX idx_pages_last_crawled ON pages(last_crawled_at);
CREATE INDEX idx_pages_is_current ON pages(is_current);
CREATE INDEX idx_entities_text ON entities(entity_text);
CREATE INDEX idx_keywords_text ON keywords(keyword_text);
CREATE INDEX idx_categories_name ON categories(category_name);
```

### Partitioning (PostgreSQL)

For large datasets, partition the `pages` table by crawl date:

```sql
CREATE TABLE pages (
    -- columns
) PARTITION BY RANGE (last_crawled_at);

CREATE TABLE pages_2025_q1 PARTITION OF pages
    FOR VALUES FROM ('2025-01-01') TO ('2025-04-01');

CREATE TABLE pages_2025_q2 PARTITION OF pages
    FOR VALUES FROM ('2025-04-01') TO ('2025-07-01');
```

### Query Optimization

**Use materialized views** for expensive queries:
```sql
CREATE MATERIALIZED VIEW category_summary AS
SELECT category_name, COUNT(*) as page_count
FROM categories c
JOIN pages p ON c.page_id = p.page_id
WHERE p.is_current = TRUE
GROUP BY category_name;

-- Refresh periodically
REFRESH MATERIALIZED VIEW category_summary;
```

---

## Migration Guide

### From JSONL to Warehouse

**Step 1**: Enable warehouse pipeline (see [Getting Started](#getting-started))

**Step 2**: Run initial load
```bash
# Set crawl version
export WAREHOUSE_CRAWL_VERSION=1

# Run Stage 3 with warehouse
python main.py --env development --stage 3
```

**Step 3**: Verify data
```bash
# Connect to SQLite
sqlite3 data/warehouse/uconn_warehouse.db

# Check counts
SELECT COUNT(*) FROM pages WHERE is_current = TRUE;
SELECT COUNT(*) FROM entities;
SELECT COUNT(*) FROM keywords;
```

### From SQLite to PostgreSQL

**Step 1**: Export from SQLite
```bash
sqlite3 data/warehouse/uconn_warehouse.db .dump > warehouse_dump.sql
```

**Step 2**: Convert SQL (SQLite → PostgreSQL syntax)
```bash
# Use pgloader or manual conversion
pip install pgloader
pgloader warehouse_dump.sql postgresql://localhost/uconn_warehouse
```

**Step 3**: Update configuration
```python
# config/production.yml
warehouse:
  db_type: postgresql
  connection_string: postgresql://warehouse_user:password@localhost:5432/uconn_warehouse
```

---

## Best Practices

### 1. Incremental Crawls
Always increment `crawl_version` on each run:
```bash
export WAREHOUSE_CRAWL_VERSION=$(($(date +%s)/86400))  # Day-based version
```

### 2. Regular Cleanup
Archive old versions periodically:
```sql
-- Keep last 5 versions
DELETE FROM pages
WHERE is_current = FALSE
  AND crawl_version < (
    SELECT MAX(crawl_version) - 5
    FROM pages
    WHERE url_hash = pages.url_hash
  );
```

### 3. Monitor Database Size
```sql
-- PostgreSQL
SELECT pg_size_pretty(pg_database_size('uconn_warehouse'));

-- SQLite
SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size();
```

### 4. Backup Strategy
```bash
# PostgreSQL
pg_dump -U warehouse_user uconn_warehouse | gzip > backup_$(date +%Y%m%d).sql.gz

# SQLite
sqlite3 data/warehouse/uconn_warehouse.db .dump | gzip > backup_$(date +%Y%m%d).sql.gz
```

---

## Troubleshooting

**Issue**: Duplicate key violations
```sql
-- Find duplicates
SELECT url_hash, COUNT(*)
FROM pages
WHERE is_current = TRUE
GROUP BY url_hash
HAVING COUNT(*) > 1;

-- Fix: Mark older versions as not current
UPDATE pages
SET is_current = FALSE
WHERE page_id NOT IN (
    SELECT MAX(page_id)
    FROM pages
    GROUP BY url_hash
);
```

**Issue**: Slow queries
```sql
-- Check index usage
EXPLAIN ANALYZE
SELECT * FROM pages WHERE url_hash = 'abc123...';

-- Rebuild indexes
REINDEX TABLE pages;
```

**Issue**: Database connection errors
```python
# Increase connection pool size
warehouse = DataWarehouse(
    db_type=DatabaseType.POSTGRESQL,
    connection_string="postgresql://user:pass@localhost/db?pool_size=20&max_overflow=40"
)
```

---

## Next Steps

1. ✅ Set up PostgreSQL for production
2. ✅ Enable warehouse pipeline in Stage 3
3. ✅ Configure vendor data integrations
4. ✅ Create dashboards for warehouse queries
5. ✅ Implement Java ETL loader for scale

**See Also**:
- [Project Internals](project_internals.md)
- [NLP Enhancements](nlp_enhancements.md)
- [Java Warehouse Loader](java_warehouse_loader.md)
