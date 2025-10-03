-- UConn Web Scraping Data Warehouse
-- Initial Schema Migration
-- Version 1.0.0

-- Pages table (fact table)
CREATE TABLE pages (
    page_id SERIAL PRIMARY KEY,
    url TEXT NOT NULL,
    url_hash VARCHAR(64) NOT NULL,
    title TEXT,
    text_content TEXT,
    word_count INTEGER DEFAULT 0,

    -- Versioning fields
    first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_crawled_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    crawl_version INTEGER DEFAULT 1,
    is_current BOOLEAN DEFAULT TRUE,

    -- Metadata
    metadata JSONB,

    CONSTRAINT unique_url_version UNIQUE(url_hash, crawl_version)
);

-- Indexes for pages
CREATE INDEX idx_pages_url_hash ON pages(url_hash);
CREATE INDEX idx_pages_is_current ON pages(is_current) WHERE is_current = TRUE;
CREATE INDEX idx_pages_last_crawled ON pages(last_crawled_at DESC);
CREATE INDEX idx_pages_crawl_version ON pages(crawl_version);

-- Entities table (dimension)
CREATE TABLE entities (
    entity_id SERIAL PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    entity_text TEXT NOT NULL,
    entity_type VARCHAR(50),
    confidence DECIMAL(3,2),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_entities_page_id ON entities(page_id);
CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE INDEX idx_entities_text ON entities(entity_text);

-- Keywords table (dimension)
CREATE TABLE keywords (
    keyword_id SERIAL PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    keyword TEXT NOT NULL,
    score DECIMAL(5,4),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_keywords_page_id ON keywords(page_id);
CREATE INDEX idx_keywords_keyword ON keywords(keyword);

-- Categories table (dimension)
CREATE TABLE categories (
    category_id SERIAL PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    category_name VARCHAR(100) NOT NULL,
    category_path VARCHAR(200),
    confidence DECIMAL(3,2),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_categories_page_id ON categories(page_id);
CREATE INDEX idx_categories_name ON categories(category_name);
CREATE INDEX idx_categories_path ON categories(category_path);

-- Page changes table (for audit trail)
CREATE TABLE page_changes (
    change_id SERIAL PRIMARY KEY,
    page_id INTEGER NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    field_name VARCHAR(50) NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    crawl_version INTEGER NOT NULL
);

CREATE INDEX idx_page_changes_page_id ON page_changes(page_id);
CREATE INDEX idx_page_changes_changed_at ON page_changes(changed_at DESC);

-- Vendor data table
CREATE TABLE vendor_data (
    vendor_id SERIAL PRIMARY KEY,
    source_name VARCHAR(100) NOT NULL,
    source_type VARCHAR(50) NOT NULL,
    data JSONB NOT NULL,
    extracted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB
);

CREATE INDEX idx_vendor_source ON vendor_data(source_name);
CREATE INDEX idx_vendor_extracted ON vendor_data(extracted_at DESC);
CREATE INDEX idx_vendor_data_gin ON vendor_data USING gin(data);

-- Crawl metadata table
CREATE TABLE crawl_metadata (
    crawl_id SERIAL PRIMARY KEY,
    crawl_version INTEGER NOT NULL UNIQUE,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'running',
    pages_processed INTEGER DEFAULT 0,
    pages_added INTEGER DEFAULT 0,
    pages_updated INTEGER DEFAULT 0,
    errors_count INTEGER DEFAULT 0,
    metadata JSONB
);

CREATE INDEX idx_crawl_version ON crawl_metadata(crawl_version);
CREATE INDEX idx_crawl_started ON crawl_metadata(started_at DESC);

-- Comments for documentation
COMMENT ON TABLE pages IS 'Main fact table containing scraped web pages with versioning';
COMMENT ON TABLE entities IS 'Named entities extracted from pages (people, organizations, locations)';
COMMENT ON TABLE keywords IS 'Keywords/keyphrases extracted from pages';
COMMENT ON TABLE categories IS 'Taxonomic categories assigned to pages';
COMMENT ON TABLE page_changes IS 'Audit trail of all content changes over time';
COMMENT ON TABLE vendor_data IS 'Data from external vendor sources (APIs, files, databases)';
COMMENT ON TABLE crawl_metadata IS 'Metadata about each crawl run';
