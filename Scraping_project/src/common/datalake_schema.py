"""
Data Lake Schema Definition

Defines the complete schema structure for the Delta Lake data warehouse.
This ensures consistent data types, partitioning, and indexes across all tables.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TableType(Enum):
    """Types of tables in the data lake"""
    RAW = "raw"  # Raw discovered URLs
    VALIDATED = "validated"  # Validated URLs with status codes
    ENRICHED = "enriched"  # Fully enriched content with NLP
    METRICS = "metrics"  # Performance and quality metrics
    GRAPH = "graph"  # URL link graph relationships


@dataclass
class DeltaLakeSchema:
    """Complete schema definition for Delta Lake tables"""

    # Discovery/Raw URLs Table
    RAW_URLS_SCHEMA = {
        'url': 'string',
        'url_hash': 'string',
        'discovered_from': 'string',
        'depth': 'int',
        'discovered_at': 'timestamp',
        'heuristic': 'string',  # How it was discovered (link, ajax, json, etc.)
        'importance_score': 'double',
        'session_id': 'string'
    }

    # Validated URLs Table
    VALIDATED_URLS_SCHEMA = {
        'url': 'string',
        'url_hash': 'string',
        'status_code': 'int',
        'content_type': 'string',
        'content_length': 'long',
        'response_time': 'double',
        'is_valid': 'boolean',
        'error_message': 'string',
        'validated_at': 'timestamp',
        'last_modified': 'string',
        'etag': 'string',
        'staleness_score': 'double',
        'cache_control': 'string',
        'validation_method': 'string',
        'redirect_chain': 'string',  # JSON array as string
        'server_headers': 'string',  # JSON object as string
        'network_metadata': 'string',  # JSON object as string
        'session_id': 'string'
    }

    # Enriched Content Table (Main analytical table)
    ENRICHED_CONTENT_SCHEMA = {
        # Core fields
        'url': 'string',
        'url_hash': 'string',
        'title': 'string',
        'description': 'string',
        'content_preview': 'string',
        'full_text': 'string',

        # NLP extracted fields
        'entities': 'string',  # JSON array
        'keywords': 'string',  # JSON array
        'categories': 'string',  # JSON array
        'content_summary': 'string',

        # Content metrics
        'word_count': 'int',
        'unique_words': 'int',
        'sentence_count': 'int',
        'avg_sentence_length': 'double',
        'readability_score': 'double',
        'language': 'string',

        # Page metadata
        'page_type': 'string',
        'department': 'string',
        'campus': 'string',
        'audience': 'string',  # student, faculty, staff, public

        # Technical fields
        'extracted_at': 'timestamp',
        'processing_time': 'double',
        'schema_version': 'string',
        'nlp_model': 'string',
        'session_id': 'string',

        # Partitioning fields
        'year': 'int',
        'month': 'int',
        'day': 'int'
    }

    # Link Graph Table
    LINK_GRAPH_SCHEMA = {
        'source_url': 'string',
        'source_hash': 'string',
        'target_url': 'string',
        'target_hash': 'string',
        'link_text': 'string',
        'link_context': 'string',
        'discovered_at': 'timestamp',
        'importance_score': 'double',
        'session_id': 'string'
    }

    # Performance Metrics Table
    PERFORMANCE_METRICS_SCHEMA = {
        'timestamp': 'timestamp',
        'stage': 'string',
        'items_processed': 'long',
        'items_per_second': 'double',
        'cpu_percent': 'double',
        'memory_mb': 'double',
        'memory_percent': 'double',
        'thread_count': 'int',
        'elapsed_seconds': 'double',
        'session_id': 'string'
    }


@dataclass
class DataLakeConfig:
    """Configuration for Delta Lake warehouse"""

    # Base paths
    BASE_PATH = "data/datalake"

    # Table paths
    RAW_URLS_PATH = f"{BASE_PATH}/raw_urls"
    VALIDATED_URLS_PATH = f"{BASE_PATH}/validated_urls"
    ENRICHED_CONTENT_PATH = f"{BASE_PATH}/enriched_content"
    LINK_GRAPH_PATH = f"{BASE_PATH}/link_graph"
    PERFORMANCE_METRICS_PATH = f"{BASE_PATH}/performance_metrics"

    # Partitioning strategies
    ENRICHED_PARTITION_COLS = ['year', 'month', 'day']  # Time-based partitioning
    METRICS_PARTITION_COLS = ['stage']  # Stage-based partitioning

    # Delta Lake options
    DELTA_OPTIONS = {
        'mode': 'append',  # Default write mode
        'mergeSchema': True,  # Allow schema evolution
        'overwriteSchema': False,  # Don't overwrite schema by default
        'dataChange': True,  # Mark as data change for CDC
    }


def get_create_table_sql(table_type: TableType) -> str:
    """Generate CREATE TABLE SQL for DuckDB/Delta Lake"""

    schemas = {
        TableType.RAW: DeltaLakeSchema.RAW_URLS_SCHEMA,
        TableType.VALIDATED: DeltaLakeSchema.VALIDATED_URLS_SCHEMA,
        TableType.ENRICHED: DeltaLakeSchema.ENRICHED_CONTENT_SCHEMA,
        TableType.GRAPH: DeltaLakeSchema.LINK_GRAPH_SCHEMA,
        TableType.METRICS: DeltaLakeSchema.PERFORMANCE_METRICS_SCHEMA
    }

    schema = schemas[table_type]
    table_name = table_type.value

    columns = []
    for col_name, col_type in schema.items():
        # Map types to DuckDB SQL types
        duckdb_type = {
            'string': 'VARCHAR',
            'int': 'INTEGER',
            'long': 'BIGINT',
            'double': 'DOUBLE',
            'boolean': 'BOOLEAN',
            'timestamp': 'TIMESTAMP'
        }.get(col_type, 'VARCHAR')

        columns.append(f"  {col_name} {duckdb_type}")

    columns_sql = ',\n'.join(columns)

    return f"""
CREATE TABLE IF NOT EXISTS {table_name} (
{columns_sql}
);
"""


def get_sample_queries() -> dict[str, str]:
    """Get sample analytical queries for the data lake"""

    return {
        'content_by_department': """
            SELECT
                department,
                COUNT(*) as page_count,
                AVG(word_count) as avg_words,
                AVG(readability_score) as avg_readability
            FROM enriched_content
            WHERE department IS NOT NULL
            GROUP BY department
            ORDER BY page_count DESC;
        """,

        'top_keywords': """
            SELECT
                keyword,
                COUNT(*) as frequency
            FROM (
                SELECT unnest(string_split(keywords, ',')) as keyword
                FROM enriched_content
            )
            GROUP BY keyword
            ORDER BY frequency DESC
            LIMIT 50;
        """,

        'validation_success_rate': """
            SELECT
                DATE_TRUNC('hour', validated_at) as hour,
                COUNT(*) as total_urls,
                SUM(CASE WHEN is_valid THEN 1 ELSE 0 END) as valid_urls,
                ROUND(100.0 * SUM(CASE WHEN is_valid THEN 1 ELSE 0 END) / COUNT(*), 2) as success_rate
            FROM validated_urls
            GROUP BY hour
            ORDER BY hour DESC
            LIMIT 24;
        """,

        'performance_trends': """
            SELECT
                stage,
                DATE_TRUNC('minute', timestamp) as minute,
                AVG(items_per_second) as avg_throughput,
                MAX(cpu_percent) as peak_cpu,
                MAX(memory_mb) as peak_memory
            FROM performance_metrics
            GROUP BY stage, minute
            ORDER BY minute DESC
            LIMIT 100;
        """,

        'link_graph_analysis': """
            SELECT
                source_url,
                COUNT(DISTINCT target_url) as outbound_links,
                AVG(importance_score) as avg_importance
            FROM link_graph
            GROUP BY source_url
            ORDER BY outbound_links DESC
            LIMIT 20;
        """
    }


if __name__ == '__main__':
    # Print all table schemas
    for table_type in TableType:
        print(get_create_table_sql(table_type))
        print()

    # Print sample queries
    print("=" * 80)
    print("SAMPLE ANALYTICAL QUERIES")
    print("=" * 80)
    queries = get_sample_queries()
    for query_name, query_sql in queries.items():
        print(f"\n## {query_name}")
        print(query_sql)
