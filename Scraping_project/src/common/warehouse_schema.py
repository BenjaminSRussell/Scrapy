"""
Data Warehouse Schema Definitions

This module defines the relational schema for the UConn web scraping data warehouse.
It supports both SQLite (development) and PostgreSQL (production) databases.

The schema is normalized to 3NF with the following tables:
- pages: Core page information with versioning
- entities: Named entities extracted from pages
- keywords: Keywords/terms extracted from pages
- categories: Taxonomy categories assigned to pages
- crawl_history: Historical crawl metadata
- vendor_data: Third-party data integrations
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

# SQL Schema Definitions

SQLITE_SCHEMA = """
-- Pages table: Core content with versioning
CREATE TABLE IF NOT EXISTS pages (
    page_id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    url_hash TEXT NOT NULL UNIQUE,
    title TEXT,
    text_content TEXT,
    word_count INTEGER DEFAULT 0,
    content_type TEXT,
    status_code INTEGER,
    has_pdf_links BOOLEAN DEFAULT FALSE,
    has_audio_links BOOLEAN DEFAULT FALSE,

    -- Timestamps and versioning
    first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_crawled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    crawl_version INTEGER DEFAULT 1,
    is_current BOOLEAN DEFAULT TRUE,

    -- Metadata
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(url_hash, crawl_version)
);

CREATE INDEX IF NOT EXISTS idx_pages_url_hash ON pages(url_hash);
CREATE INDEX IF NOT EXISTS idx_pages_last_crawled ON pages(last_crawled_at);
CREATE INDEX IF NOT EXISTS idx_pages_is_current ON pages(is_current);

-- Entities table: Named entities from NLP
CREATE TABLE IF NOT EXISTS entities (
    entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    entity_text TEXT NOT NULL,
    entity_type TEXT,
    confidence REAL DEFAULT 1.0,
    source TEXT DEFAULT 'nlp',  -- 'nlp', 'transformer', 'glossary', 'deberta'

    crawl_version INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_entities_page_id ON entities(page_id);
CREATE INDEX IF NOT EXISTS idx_entities_text ON entities(entity_text);

-- Keywords table: Keywords/terms
CREATE TABLE IF NOT EXISTS keywords (
    keyword_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    keyword_text TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    relevance_score REAL DEFAULT 1.0,
    source TEXT DEFAULT 'nlp',  -- 'nlp', 'glossary', 'manual'

    crawl_version INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_keywords_page_id ON keywords(page_id);
CREATE INDEX IF NOT EXISTS idx_keywords_text ON keywords(keyword_text);

-- Categories table: Taxonomy classifications
CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    category_name TEXT NOT NULL,
    category_path TEXT,  -- Hierarchical path like "healthcare.medical_education"
    confidence_score REAL DEFAULT 1.0,
    matched_keywords TEXT,  -- JSON array of keywords that triggered this category

    crawl_version INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_categories_page_id ON categories(page_id);
CREATE INDEX IF NOT EXISTS idx_categories_name ON categories(category_name);

-- Crawl history: Track crawl sessions
CREATE TABLE IF NOT EXISTS crawl_history (
    crawl_id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stage TEXT NOT NULL,  -- 'discovery', 'validation', 'enrichment'
    pages_processed INTEGER DEFAULT 0,
    pages_successful INTEGER DEFAULT 0,
    pages_failed INTEGER DEFAULT 0,
    duration_seconds REAL,
    status TEXT DEFAULT 'running',  -- 'running', 'completed', 'failed'
    error_message TEXT,

    -- Configuration snapshot
    config_snapshot TEXT,  -- JSON of configuration used

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_crawl_history_timestamp ON crawl_history(crawl_timestamp);
CREATE INDEX IF NOT EXISTS idx_crawl_history_stage ON crawl_history(stage);

-- Vendor data: Third-party integrations
CREATE TABLE IF NOT EXISTS vendor_data (
    vendor_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER,
    vendor_name TEXT NOT NULL,
    vendor_url TEXT,
    data_type TEXT NOT NULL,  -- 'api', 'extract', 'manual'
    raw_data TEXT,  -- JSON blob of vendor data
    extracted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Link to page if applicable
    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_vendor_data_page_id ON vendor_data(page_id);
CREATE INDEX IF NOT EXISTS idx_vendor_data_vendor ON vendor_data(vendor_name);

-- Page changes: Track content changes over time
CREATE TABLE IF NOT EXISTS page_changes (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id INTEGER NOT NULL,
    previous_version INTEGER,
    current_version INTEGER NOT NULL,
    change_type TEXT NOT NULL,  -- 'title', 'content', 'metadata'
    old_value TEXT,
    new_value TEXT,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_page_changes_page_id ON page_changes(page_id);
CREATE INDEX IF NOT EXISTS idx_page_changes_timestamp ON page_changes(changed_at);
"""

POSTGRESQL_SCHEMA = """
-- Pages table: Core content with versioning
CREATE TABLE IF NOT EXISTS pages (
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

    -- Timestamps and versioning
    first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_crawled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    crawl_version INTEGER DEFAULT 1,
    is_current BOOLEAN DEFAULT TRUE,

    -- Metadata
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(url_hash, crawl_version)
);

CREATE INDEX IF NOT EXISTS idx_pages_url_hash ON pages(url_hash);
CREATE INDEX IF NOT EXISTS idx_pages_last_crawled ON pages(last_crawled_at);
CREATE INDEX IF NOT EXISTS idx_pages_is_current ON pages(is_current);

-- Entities table: Named entities from NLP
CREATE TABLE IF NOT EXISTS entities (
    entity_id SERIAL PRIMARY KEY,
    page_id INTEGER NOT NULL,
    entity_text TEXT NOT NULL,
    entity_type TEXT,
    confidence REAL DEFAULT 1.0,
    source TEXT DEFAULT 'nlp',

    crawl_version INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_entities_page_id ON entities(page_id);
CREATE INDEX IF NOT EXISTS idx_entities_text ON entities(entity_text);

-- Keywords table: Keywords/terms
CREATE TABLE IF NOT EXISTS keywords (
    keyword_id SERIAL PRIMARY KEY,
    page_id INTEGER NOT NULL,
    keyword_text TEXT NOT NULL,
    frequency INTEGER DEFAULT 1,
    relevance_score REAL DEFAULT 1.0,
    source TEXT DEFAULT 'nlp',

    crawl_version INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_keywords_page_id ON keywords(page_id);
CREATE INDEX IF NOT EXISTS idx_keywords_text ON keywords(keyword_text);

-- Categories table: Taxonomy classifications
CREATE TABLE IF NOT EXISTS categories (
    category_id SERIAL PRIMARY KEY,
    page_id INTEGER NOT NULL,
    category_name TEXT NOT NULL,
    category_path TEXT,
    confidence_score REAL DEFAULT 1.0,
    matched_keywords JSONB,

    crawl_version INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_categories_page_id ON categories(page_id);
CREATE INDEX IF NOT EXISTS idx_categories_name ON categories(category_name);
CREATE INDEX IF NOT EXISTS idx_categories_keywords ON categories USING GIN (matched_keywords);

-- Crawl history: Track crawl sessions
CREATE TABLE IF NOT EXISTS crawl_history (
    crawl_id SERIAL PRIMARY KEY,
    crawl_timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stage TEXT NOT NULL,
    pages_processed INTEGER DEFAULT 0,
    pages_successful INTEGER DEFAULT 0,
    pages_failed INTEGER DEFAULT 0,
    duration_seconds REAL,
    status TEXT DEFAULT 'running',
    error_message TEXT,

    config_snapshot JSONB,

    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_crawl_history_timestamp ON crawl_history(crawl_timestamp);
CREATE INDEX IF NOT EXISTS idx_crawl_history_stage ON crawl_history(stage);

-- Vendor data: Third-party integrations
CREATE TABLE IF NOT EXISTS vendor_data (
    vendor_id SERIAL PRIMARY KEY,
    page_id INTEGER,
    vendor_name TEXT NOT NULL,
    vendor_url TEXT,
    data_type TEXT NOT NULL,
    raw_data JSONB,
    extracted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_vendor_data_page_id ON vendor_data(page_id);
CREATE INDEX IF NOT EXISTS idx_vendor_data_vendor ON vendor_data(vendor_name);
CREATE INDEX IF NOT EXISTS idx_vendor_data_raw ON vendor_data USING GIN (raw_data);

-- Page changes: Track content changes over time
CREATE TABLE IF NOT EXISTS page_changes (
    change_id SERIAL PRIMARY KEY,
    page_id INTEGER NOT NULL,
    previous_version INTEGER,
    current_version INTEGER NOT NULL,
    change_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (page_id) REFERENCES pages(page_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_page_changes_page_id ON page_changes(page_id);
CREATE INDEX IF NOT EXISTS idx_page_changes_timestamp ON page_changes(changed_at);
"""


class DatabaseType(Enum):
    """Supported database types"""
    SQLITE = "sqlite"
    POSTGRESQL = "postgresql"


@dataclass
class PageRecord:
    """Normalized page record for data warehouse"""
    url: str
    url_hash: str
    title: str | None = None
    text_content: str | None = None
    word_count: int = 0
    content_type: str | None = None
    status_code: int | None = None
    has_pdf_links: bool = False
    has_audio_links: bool = False

    # Versioning
    first_seen_at: datetime = field(default_factory=datetime.now)
    last_crawled_at: datetime = field(default_factory=datetime.now)
    crawl_version: int = 1
    is_current: bool = True

    # Auto-generated
    page_id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class EntityRecord:
    """Named entity record"""
    page_id: int
    entity_text: str
    entity_type: str | None = None
    confidence: float = 1.0
    source: str = "nlp"
    crawl_version: int = 1

    entity_id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class KeywordRecord:
    """Keyword record"""
    page_id: int
    keyword_text: str
    frequency: int = 1
    relevance_score: float = 1.0
    source: str = "nlp"
    crawl_version: int = 1

    keyword_id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class CategoryRecord:
    """Category/taxonomy record"""
    page_id: int
    category_name: str
    category_path: str | None = None
    confidence_score: float = 1.0
    matched_keywords: list[str] = field(default_factory=list)
    crawl_version: int = 1

    category_id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class CrawlHistoryRecord:
    """Crawl session history"""
    crawl_timestamp: datetime
    stage: str
    pages_processed: int = 0
    pages_successful: int = 0
    pages_failed: int = 0
    duration_seconds: float | None = None
    status: str = "running"
    error_message: str | None = None
    config_snapshot: dict[str, Any] | None = None

    crawl_id: int | None = None
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None


@dataclass
class VendorDataRecord:
    """Third-party vendor data"""
    vendor_name: str
    data_type: str
    raw_data: dict[str, Any]
    page_id: int | None = None
    vendor_url: str | None = None
    extracted_at: datetime = field(default_factory=datetime.now)

    vendor_id: int | None = None


@dataclass
class PageChangeRecord:
    """Page content change tracking"""
    page_id: int
    current_version: int
    change_type: str
    new_value: str | None = None
    old_value: str | None = None
    previous_version: int | None = None
    changed_at: datetime = field(default_factory=datetime.now)

    change_id: int | None = None


def get_schema_sql(db_type: DatabaseType) -> str:
    """Get SQL schema for specified database type"""
    if db_type == DatabaseType.POSTGRESQL:
        return POSTGRESQL_SCHEMA
    return SQLITE_SCHEMA
