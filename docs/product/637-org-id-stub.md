# Org id on crawl jobs and profiles

- **Status:** Stub. In-memory product records only. Not tenant isolation.
- **Date:** 2026-10-14
- **Issue:** [#637](https://github.com/BenjaminSRussell/Scrapy/issues/637)

## What it does

[`org_catalog.py`](../../Scraping_project/src/utils/org_catalog.py) stores a crawl profile and a crawl job with an `org_id`.

| Call | Org |
|---|---|
| Create a profile with no `org_id`, or a blank one | `local` |
| Create a job with no `org_id`, or a blank one | the profile's org |
| List profiles or jobs with no org | only `local` |
| List with an org | only that org |

`local` is not the site-id fallback `default`. A job whose `org_id` is not its profile's org is rejected. The profile is not moved.

Ids match `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. The catalog object is the whole store. A restart drops it. `to_document` writes `org_id` on every record.

## What it does not do

- It does not change Redis keys ([#191](https://github.com/BenjaminSRussell/Scrapy/issues/191)), metric labels ([#192](https://github.com/BenjaminSRussell/Scrapy/issues/192)), or Delta layout ([#193](https://github.com/BenjaminSRussell/Scrapy/issues/193)). Two orgs still share seen-sets, queues, and lake tables.
- `org_id` is not an access grant. The [federated gold-query stub](federated-tenant-gold-query.md) and the [GraphQL gold draft](749-graphql-gold.md) already say so.
- It is not the crawl job API ([#147](https://github.com/BenjaminSRussell/Scrapy/issues/147)): no HTTP route, no scheduler, no Postgres or Delta job table, no pause or cancel.

No SLO, alert, dashboard series, or metric is added here.
