# Lake `tenant_id` (#670)

Every Delta table the Python lake writer creates or appends to carries a nullable string column `tenant_id`. This is a stub: one crawl profile stamps one id. It does not enforce isolation, and it is not a partition key.

## What new writes do

`LakehouseManager.write` and `merge_into` stamp the column in `_enrich_records` before the commit.

1. A non-empty `tenant_id` already on the row is kept when it matches `[A-Za-z0-9][A-Za-z0-9._-]{0,127}` (the same alphabet as site ids).
2. Otherwise the row gets the crawl profile's tenant: `DELTA_TENANT_ID` if that is set, else `delta_lake.tenant_id` in `config.yml`, else `default`.
3. A blank or invalid profile value is `default`. The write is not refused.

The registered schemas in `src/core/schemas.py` all include the column as a nullable string (`("tenant_id", pa.string())`, which is nullable). The Arrow field written to a new table is nullable too, so a later file can still omit it. The in-memory test backend does not stamp; it is not a lake writer.

`docs/contracts/` does not list `tenant_id`. Those files are the cross-repo consumer contracts. Manager-stamped columns (`tenant_id`, `_ingestion_time`, `_stage`) stay out of them, and `additionalProperties` stays true so a reader can use the column or ignore it.

`kafka-delta-ingest` writes its own column list (the bronze contract: `url`, `scraped_at_utc`, `spider_name`, plus the optional fields). It does not stamp `tenant_id`. Those rows stay null and are treated as legacy rows below.

## Querying

`read()` is unchanged and returns every tenant.

```python
from src.lakehouse.lakehouse_manager import LakehouseManager, filter_rows_by_tenant

lake = LakehouseManager(start_workers=False)
rows = lake.read_for_tenant("stage2_page_analysis")          # profile tenant
just_acme = lake.read_for_tenant("stage2_page_analysis", "acme")
```

`filter_rows_by_tenant(rows, tenant_id)` is the same check on a list you already have. For the `default` tenant, null, missing, and blank values are included: they are rows written before this column existed. For any other tenant they are excluded, unless you pass `include_legacy=True`. `tenant_id` is not a partition column, so this filters scanned rows; it does not prune files by itself.

## Migrating an existing lake

Nothing rewrites old files on deploy. The next append or merge adds `tenant_id` through schema evolution. Rows already in the table come back as null. New rows carry the profile id. Leaving the nulls in place is safe for a single-tenant install: `read_for_tenant` without an argument includes them as `default`.

Backfill only when you need nulls to become a real id (for example before a second tenant must not see them). From `Scraping_project/`, against a copy of the table first:

```python
from deltalake import DeltaTable

path = "data/delta_lake/stage2_page_analysis"  # repeat per table
dt = DeltaTable(path)
# The column appears on the next Python append. Update only fills nulls after that.
if "tenant_id" in dt.schema().to_arrow().names:
    dt.update(new_values={"tenant_id": "default"}, predicate="tenant_id IS NULL")
```

`new_values` is a Python value, not a SQL expression. Run it once per table directory under the lake root, including quarantine and `*_history` tables if those should carry the same id. Do not vacuum in the same step. Time travel still shows the pre-update version until retention expires.

There is no automatic migration. Two profiles that both stay on `default` still share one lake, the same way two profiles on the `default` site id share one seen-set.

Tables are not repartitioned by `tenant_id`. An existing table keeps the partition columns in its `_delta_log`.
