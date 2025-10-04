-- DuckDB Setup Script for UConn Pipeline Data Lake
-- Generated automatically by init_datalake.py

-- Load Delta Lake extension
INSTALL delta;
LOAD delta;

-- Create views for each table

CREATE OR REPLACE VIEW raw_urls AS
  SELECT * FROM delta_scan('data/datalake/raw_urls');

CREATE OR REPLACE VIEW validated_urls AS
  SELECT * FROM delta_scan('data/datalake/validated_urls');

CREATE OR REPLACE VIEW enriched_content AS
  SELECT * FROM delta_scan('data/datalake/enriched_content');

CREATE OR REPLACE VIEW link_graph AS
  SELECT * FROM delta_scan('data/datalake/link_graph');

CREATE OR REPLACE VIEW performance_metrics AS
  SELECT * FROM delta_scan('data/datalake/performance_metrics');
