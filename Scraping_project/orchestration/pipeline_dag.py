"""
Apache Airflow DAG for orchestrating the UConn web scraping pipeline.

This DAG defines the complete workflow:
1. Run Python scraping (Stage 1-3)
2. Only if successful, run Java ETL loader
3. Validate data quality
4. Send monitoring alerts

To use this DAG:
1. Install Airflow: pip install apache-airflow
2. Copy this file to your Airflow DAGs folder (usually ~/airflow/dags/)
3. Configure the variables in Airflow UI or via environment variables
"""

from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.utils.task_group import TaskGroup

# Default arguments for all tasks
default_args = {
    'owner': 'data-team',
    'depends_on_past': False,
    'email': ['alerts@uconn.edu'],
    'email_on_failure': True,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
    'execution_timeout': timedelta(hours=2),
}

# DAG definition
dag = DAG(
    'uconn_scraping_pipeline',
    default_args=default_args,
    description='UConn web scraping and ETL pipeline',
    schedule_interval='0 2 * * *',  # Run daily at 2 AM
    start_date=datetime(2025, 1, 1),
    catchup=False,
    max_active_runs=1,
    tags=['scraping', 'etl', 'uconn'],
)

# Configuration
PROJECT_DIR = Path('/path/to/Scraping_project')  # Update this path
ENRICHED_DATA_DIR = PROJECT_DIR / 'data' / 'enriched'
JAVA_ETL_JAR = PROJECT_DIR / 'java-etl-loader' / 'target' / 'warehouse-etl-loader-1.0.0.jar'


def check_scraped_data(**context):
    """Validate that scraping produced output files."""

    enriched_files = list(ENRICHED_DATA_DIR.glob('*.jsonl'))

    if not enriched_files:
        raise ValueError(f"No enriched data files found in {ENRICHED_DATA_DIR}")

    total_records = 0
    for file in enriched_files:
        with open(file) as f:
            total_records += sum(1 for _ in f)

    if total_records == 0:
        raise ValueError("Scraped files contain no records")

    # Push metrics to XCom for downstream tasks
    context['task_instance'].xcom_push(key='total_records', value=total_records)
    context['task_instance'].xcom_push(key='files_count', value=len(enriched_files))

    print(f"✓ Validation passed: {total_records} records across {len(enriched_files)} files")
    return total_records


def check_warehouse_data(**context):
    """Validate that data was loaded into warehouse."""
    postgres_hook = PostgresHook(postgres_conn_id='uconn_warehouse')

    # Get total pages loaded
    result = postgres_hook.get_first(
        "SELECT COUNT(*) FROM pages WHERE is_current = TRUE"
    )
    current_pages = result[0] if result else 0

    # Get latest crawl metadata
    crawl_result = postgres_hook.get_first("""
        SELECT crawl_version, pages_processed, pages_added, pages_updated
        FROM crawl_metadata
        ORDER BY started_at DESC
        LIMIT 1
    """)

    if not crawl_result:
        raise ValueError("No crawl metadata found in warehouse")

    crawl_version, processed, added, updated = crawl_result

    context['task_instance'].xcom_push(key='current_pages', value=current_pages)
    context['task_instance'].xcom_push(key='crawl_version', value=crawl_version)
    context['task_instance'].xcom_push(key='pages_processed', value=processed)

    print(f"✓ Warehouse validation passed:")
    print(f"  - Current pages: {current_pages}")
    print(f"  - Crawl version: {crawl_version}")
    print(f"  - Processed: {processed}, Added: {added}, Updated: {updated}")

    return current_pages


# Task Group: Python Scraping Pipeline
with TaskGroup('python_scraping', dag=dag) as python_scraping_group:

    # Stage 1: Discovery
    stage1_discovery = BashOperator(
        task_id='stage1_discovery',
        bash_command=f"""
        cd {PROJECT_DIR}
        python -m scrapy crawl discovery_spider \
            -s CLOSESPIDER_PAGECOUNT=1000 \
            -s CONCURRENT_REQUESTS=16
        """,
    )

    # Stage 2: Validation
    stage2_validation = BashOperator(
        task_id='stage2_validation',
        bash_command=f"""
        cd {PROJECT_DIR}
        python src/stage2/validator.py
        """,
    )

    # Stage 3: Enrichment
    stage3_enrichment = BashOperator(
        task_id='stage3_enrichment',
        bash_command=f"""
        cd {PROJECT_DIR}
        python -m scrapy crawl enrichment_spider \
            -s CONCURRENT_REQUESTS=8
        """,
    )

    # Check scraped data quality
    validate_scraped_data = PythonOperator(
        task_id='validate_scraped_data',
        python_callable=check_scraped_data,
        provide_context=True,
    )

    # Define task dependencies within the group
    stage1_discovery >> stage2_validation >> stage3_enrichment >> validate_scraped_data


# Task: Java ETL Loader
java_etl_load = BashOperator(
    task_id='java_etl_load',
    bash_command=f"""
    cd {PROJECT_DIR / 'java-etl-loader'}
    java -jar {JAVA_ETL_JAR} \
        --spring.datasource.url=${{DATABASE_URL}} \
        --spring.datasource.username=${{DATABASE_USER}} \
        --spring.datasource.password=${{DATABASE_PASSWORD}} \
        --etl.input.directory={ENRICHED_DATA_DIR}
    """,
    dag=dag,
)

# Task: Validate warehouse data
validate_warehouse = PythonOperator(
    task_id='validate_warehouse',
    python_callable=check_warehouse_data,
    provide_context=True,
    dag=dag,
)

# Task: Run data quality checks
data_quality_checks = PostgresOperator(
    task_id='data_quality_checks',
    postgres_conn_id='uconn_warehouse',
    sql="""
    -- Check for pages without entities
    DO $$
    DECLARE
        orphan_pages INTEGER;
    BEGIN
        SELECT COUNT(*) INTO orphan_pages
        FROM pages p
        WHERE p.is_current = TRUE
          AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.page_id = p.page_id);

        IF orphan_pages > 100 THEN
            RAISE WARNING 'Found % pages without entities', orphan_pages;
        END IF;
    END $$;

    -- Check for duplicate URLs
    DO $$
    DECLARE
        duplicate_urls INTEGER;
    BEGIN
        SELECT COUNT(*) INTO duplicate_urls
        FROM (
            SELECT url_hash
            FROM pages
            WHERE is_current = TRUE
            GROUP BY url_hash
            HAVING COUNT(*) > 1
        ) dups;

        IF duplicate_urls > 0 THEN
            RAISE EXCEPTION 'Found % duplicate URLs marked as current', duplicate_urls;
        END IF;
    END $$;
    """,
    dag=dag,
)

# Task: Cleanup old data
cleanup_old_versions = PostgresOperator(
    task_id='cleanup_old_versions',
    postgres_conn_id='uconn_warehouse',
    sql="""
    -- Delete page versions older than 90 days that are not current
    DELETE FROM pages
    WHERE is_current = FALSE
      AND last_crawled_at < NOW() - INTERVAL '90 days';

    -- Vacuum to reclaim space
    VACUUM ANALYZE pages;
    VACUUM ANALYZE entities;
    VACUUM ANALYZE keywords;
    VACUUM ANALYZE categories;
    """,
    dag=dag,
)

# Task: Generate metrics report
generate_metrics = BashOperator(
    task_id='generate_metrics',
    bash_command=f"""
    cd {PROJECT_DIR}
    python orchestration/generate_metrics_report.py \
        --output data/reports/metrics_{{{{ ds }}}}.json
    """,
    dag=dag,
)

# Define DAG dependencies (the workflow)
python_scraping_group >> java_etl_load >> validate_warehouse
validate_warehouse >> [data_quality_checks, generate_metrics]
data_quality_checks >> cleanup_old_versions

# Optional: Add failure callback
def send_failure_alert(context):
    """Send alert on DAG failure."""
    task_instance = context['task_instance']
    dag_run = context['dag_run']

    print(f"❌ FAILURE ALERT:")
    print(f"  Task: {task_instance.task_id}")
    print(f"  DAG: {dag_run.dag_id}")
    print(f"  Execution date: {dag_run.execution_date}")
    print(f"  Log URL: {task_instance.log_url}")

    # Here you would send to Slack, PagerDuty, email, etc.


# Attach failure callback to DAG
dag.on_failure_callback = send_failure_alert
