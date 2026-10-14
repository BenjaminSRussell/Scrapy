# Federated query across tenant gold lakes

- **Status:** Stub. Design only. No route, handler, client, or config key is added here.
- **Date:** 2026-10-14
- **Issue:** [#770](https://github.com/BenjaminSRussell/Scrapy/issues/770)

This file does not change runtime behavior. It does not implement an API.
It records the product shape of a read-only federated query over tenant gold
lakes, and the ACL rules that query must meet before any implementation.

The umbrella-org case is the reason: an operator who is allowed to see more
than one tenant needs one search across those tenants' gold lakes. Isolation
stays on disk. Federation is a query-time union, not a copy into a shared lake.

No tenant count is assumed. The tenant set is whatever the caller's grants
name. No query latency is stated. None has been measured, and this stub does
not set a target.

## What exists today

The lake is one root. `DELTA_LAKE_PATH` wins when set; otherwise
`delta_lake.base_path` in `Scraping_project/config.yml` (default
`./data/delta_lake`). Tables are directories under that root. The list and
how to read it are in the
[data usage guide](../../Scraping_project/docs/guides/DATA_USAGE.md).
`LakehouseManager` (`Scraping_project/src/lakehouse/lakehouse_manager.py`)
is the writer. There is no second root per tenant.

Medallion layering is not in the tree. Bronze, silver, and gold names appear
as a later roadmap item in [docs/ROADMAP.md](../ROADMAP.md) and as open work
in [#303](https://github.com/BenjaminSRussell/Scrapy/issues/303) (bronze =
raw append, silver = validated/typed, gold = aggregates). Stage tables
(`stage2_page_analysis`, `stage3_summaries`, `stage4_large_doc_summaries`,
and the rest on the [stage map](../STAGE_MAP.md)) are not a gold layer.
This design does not relabel them as gold.

Per-tenant path prefixes are also open, not shipped.
[#671](https://github.com/BenjaminSRussell/Scrapy/issues/671) proposes an
optional `base_path/{tenant_id}/...` layout behind a flag, with today's
single root kept as the default. [#670](https://github.com/BenjaminSRussell/Scrapy/issues/670)
proposes a nullable `tenant_id` column (default `default`) on lake rows.
Neither is present. A `tenant_id` filter on the current flat lake is not
federation and is not a substitute for it.

What does exist is a site/tenant id on Redis seen-sets
([Redis capacity](../../Scraping_project/docs/REDIS_CAPACITY.md)): the id is
the argument, else `SITE_ID`, else `TENANT_ID`, else `REDIS_SITE_ID`, else
`default`. Two profiles that share an id share a set. That id is not a lake
path and not an authorization grant.
[ADR-0001](../adr/0001-config-yml-is-canonical.md) says per-tenant
configuration, if it is needed later, would be layered files, not more
environment variables. This stub does not add those files.

## Gold lake, for this query

A tenant gold lake is the curated (gold) tables for one tenant, under that
tenant's own lake prefix, once [#303](https://github.com/BenjaminSRussell/Scrapy/issues/303)
and [#671](https://github.com/BenjaminSRussell/Scrapy/issues/671) exist.

Until both land, the federated query has nothing it is allowed to open. It
must not scan the shared `delta_lake.base_path`, bronze, silver, queues, or
quarantine tables in their place.

Rules the later layout has to satisfy for this query to be well-defined:

- One tenant, one prefix. The prefix is chosen by the deployment, not by the
  query string.
- Gold tables live only under that prefix. Bronze and silver may share the
  prefix, but they are not in the federated read set.
- The tenant id on the path is the same id writers stamp, and the same shape
  Redis already accepts for a site/tenant id. This stub does not define a
  second id space.
- The unset / `default` id is one lake. It is not "every tenant".

## Federated query

The query is a read. It fans out to gold prefixes and merges hits. It does
not write a merged Delta table, a cache shared across principals, or a row
into any tenant's lake.

Conceptual inputs (not a request schema, and not implemented):

| Input | Rule |
|---|---|
| Principal | Required. Anonymous calls are denied before any path is resolved. |
| Tenant set | Optional. Empty means "every tenant this principal is granted". An explicit list is intersected with the grants. Ids the principal is not granted are dropped, not looked up on disk. |
| Gold table set | Optional. Empty means "every table the gold contract marks as gold". A name outside that contract is rejected. Stage-table names from today's lake are outside the contract. |
| Query | The search expression. Its grammar is not chosen here. It cannot carry a filesystem path, a lake URI, or a tenant id that bypasses the tenant set. |
| Row limit | Required on every call so a query cannot scan unbounded gold. This stub does not pick the number. |
| Version pin | Omitted in the stub. If a later design allows time travel, it is per tenant and still under the same grants. |

Planning order:

1. Authenticate the principal. Failure stops the call. No tenant id is echoed
   back to an unauthenticated caller.
2. Resolve grants. The authorized set is the intersection of the request and
   the grants. No storage client is opened in this step.
3. Resolve each authorized tenant to its gold prefix from the deployment
   layout. The caller does not supply the path.
4. Reject the call if the authorized set is empty, or if a requested gold
   table is not in the gold contract. That rejection lists only ids the
   principal was already allowed to name.

Execution:

- Each authorized tenant is read independently, gold tables only.
- Rows are merged by appending them. Each row carries the tenant id it was
  read from, even if the gold table also stores that column.
- There is no cross-tenant join and no write of the merge.
- One tenant's missing prefix or read error is a status on that tenant. It
  does not drop other tenants' rows and does not synthesize rows.
- Order across tenants is undefined. This stub does not rank hits and does
  not claim a latency.

Response shape, still not implemented: the rows that were read, plus one
status per tenant the caller was allowed to know about (`read`, `empty`,
`failed`). Tenants the caller is not granted do not appear. A grant plus a
missing prefix is `failed` or `empty` for that tenant only; it is not a
signal other tenants exist.

[#935](https://github.com/BenjaminSRussell/Scrapy/issues/935) is a different
product (search over job summaries, permission-aware later). This stub does
not satisfy it and does not point the federated query at Postgres full-text
search. Org ids on jobs ([#637](https://github.com/BenjaminSRussell/Scrapy/issues/637))
are not grants. A job's `org_id`, once it exists, does not by itself authorize
a gold read.

## ACL requirements

Default is deny. A missing grant, an unknown id, and a typo are the same
result for a principal who lacks the grant: no rows, and no indication of
whether a tenant or a prefix exists.

1. **Authentication.** Every call has an authenticated principal. There is no
   anonymous, shared, or "ops network" bypass.
2. **Explicit grant per tenant.** A principal reads a tenant's gold lake only
   when a grant names that principal (or a role the principal holds) and that
   tenant. Membership in an umbrella org is not a grant. An org-wide role is
   not implied by [#637](https://github.com/BenjaminSRussell/Scrapy/issues/637).
3. **Umbrella grant is an allow-list.** If a role may search several tenants,
   the role's grant lists those tenants. It is not "all tenants that exist
   now or later". Adding a tenant does not extend old grants.
4. **Gold only.** A grant for this query is gold-read. It does not authorize
   bronze, silver, queues, quarantine, exports, or writes. A principal who
   can administer a tenant's crawl still cannot pull those paths through
   federation.
5. **Path confinement.** The resolver opens only the gold prefix for a
   granted tenant. It refuses caller-supplied paths, `..` segments, and
   symlinks that leave the prefix. A storage credential used by the query
   worker can read those gold prefixes and nothing else in the lake.
6. **No existence oracle.** Before a grant exists, the API does not reveal
   whether the tenant id is known, whether a prefix is on disk, or whether
   another principal can see it. Ids the caller is allowed to name may be
   reflected; others are omitted.
7. **Column allow-list.** Returned columns are the gold contract's columns
   plus the tenant id the planner attached. Raw page bodies, credentials,
   queue internals, and error payloads are not gold and are not returned.
   The contract itself is [#303](https://github.com/BenjaminSRussell/Scrapy/issues/303)'s
   job; this stub does not name columns that are not decided.
8. **Row limit is not an ACL.** The required limit caps work. It does not
   replace a grant. Quotas ([#672](https://github.com/BenjaminSRussell/Scrapy/issues/672))
   are a separate fairness control. A denied call must not consume another
   tenant's quota. A quota rejection is not an authorization decision and
   uses a different error from deny.
9. **Audit.** Each call appends principal, requested tenant ids, authorized
   tenant ids actually opened, and deny or failure reason. The audit log is
   not a gold table and is not readable through this query. A principal
   cannot use federation to see who else queried a tenant.
10. **No shared result cache.** If results are cached, the cache key includes
    the principal's authorized tenant set (or caches are per tenant and merged
    only after the ACL step). A cache keyed only by query text is forbidden:
    a later caller would otherwise receive an earlier caller's rows.
11. **Read-only.** Federation cannot promote, delete, vacuum, or export.
    Time travel, if added later, stays inside the same grants and the same
    gold prefixes.
12. **Grant changes apply to the next call.** A revoked grant is not honored
    from a cache or an in-flight fan-out that has not opened that tenant yet.
    This stub does not define how fast a revocation reaches a call that has
    already opened the prefix.
13. **`default` is not public.** The fallback tenant id used when `TENANT_ID`
    is unset is a normal tenant for ACL purposes. It is not world-readable.

Storage and Kubernetes isolation ([#676](https://github.com/BenjaminSRussell/Scrapy/issues/676),
namespace examples) are deployment controls. They do not replace the grants
above. A process that can mount every tenant volume is still not allowed to
return another tenant's rows through this query.

## Out of scope

- Implementing the query, a Python helper, a config flag, a Helm value, or a
  dashboard search box.
- Choosing gold table names, partition columns, or a query grammar.
- Choosing a row-limit number, a timeout, or a latency target.
- Copying tenant gold into one shared table to make search easier.
- Treating [#935](https://github.com/BenjaminSRussell/Scrapy/issues/935) job-summary
  search as this feature.
- Per-tenant crawl quotas, except the rule that a deny must not spend them.

## Related

- [#770](https://github.com/BenjaminSRussell/Scrapy/issues/770) — this stub.
- [#303](https://github.com/BenjaminSRussell/Scrapy/issues/303) — bronze / silver / gold layering. Gold is undefined until that lands.
- [#671](https://github.com/BenjaminSRussell/Scrapy/issues/671) — per-tenant Delta prefix. Federation reads that layout; it does not create it.
- [#670](https://github.com/BenjaminSRussell/Scrapy/issues/670) — `tenant_id` column on lake tables.
- [#637](https://github.com/BenjaminSRussell/Scrapy/issues/637) — `org_id` on jobs and profiles. Not an ACL grant.
- [#672](https://github.com/BenjaminSRussell/Scrapy/issues/672) — tenant quotas. Not an ACL.
- [#935](https://github.com/BenjaminSRussell/Scrapy/issues/935) — federated search across job summaries. Separate product.
- [Data usage guide](../../Scraping_project/docs/guides/DATA_USAGE.md) — today's single lake root.
- [Roadmap](../ROADMAP.md) — bronze/silver/gold is listed under later work.
- [ADR-0001](../adr/0001-config-yml-is-canonical.md) — canonical config; per-tenant config is explicitly not decided.
