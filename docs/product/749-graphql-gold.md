# GraphQL read API over gold aggregates

- **Status:** Draft. Design only. No route, schema package, handler, or config key is added here.
- **Date:** 2026-10-14
- **Issue:** [#749](https://github.com/BenjaminSRussell/Scrapy/issues/749)

This file does not change runtime behavior. It does not implement an API.
Nothing in the tree serves the schema below. The read API is disabled by
default, including by the absence of any switch.

The shape frontends asked for is a nested read: job, then stage, then docs.
That nesting is the whole schema. It is a read. There is no mutation and no
subscription.

[#749](https://github.com/BenjaminSRussell/Scrapy/issues/749) names strawberry
or ariadne as optional libraries for a later implementation. This draft does
not choose either one. It does not add those packages, or any other GraphQL
library, to a requirements file. The SDL in this file is text. No program
imports it.

No tenant count is assumed. The caller sees only jobs a grant allows, not a
fixed population. No query latency is stated. None has been measured, and
this draft does not set a target.

## What exists today

The lake is one root. `DELTA_LAKE_PATH` wins when set; otherwise
`delta_lake.base_path` in `Scraping_project/config.yml` (default
`./data/delta_lake`). Tables and how to read them are in the
[data usage guide](../../Scraping_project/docs/guides/DATA_USAGE.md).
`LakehouseManager`
(`Scraping_project/src/lakehouse/lakehouse_manager.py`) is the writer.
There is no GraphQL server, no `api/` package, and no gold-aggregate table
named job, stage, or doc.

Medallion layering is not in the tree. Bronze, silver, and gold are later
roadmap work in [docs/ROADMAP.md](../ROADMAP.md) and open work in
[#303](https://github.com/BenjaminSRussell/Scrapy/issues/303) (bronze = raw
append, silver = validated/typed, gold = aggregates). Stage tables on the
[stage map](../STAGE_MAP.md) are not a gold layer. This draft does not
relabel them as gold, and a future resolver must not answer these types by
reading those tables under gold names.

A crawl job id already exists on pipeline spans as `scrapy.crawl_job_id`
(`Scraping_project/src/otel_tracing.py`). This draft uses that id for `Job`
when gold rows are later keyed by it. It does not define a second id, and it
does not claim those rows exist today.

The [federated gold-query stub](federated-tenant-gold-query.md) is a
different product: one search across tenant gold lakes. This file does not
implement that search and does not satisfy it.

## Schema draft

The draft is the SDL in this section. Field sets are identity, nesting, and
pagination only. Counts, status words, titles, URLs, and summary text are
not fields here. [#303](https://github.com/BenjaminSRussell/Scrapy/issues/303)
has not named gold columns, and this draft does not invent them. Raw page
bodies are not gold and are not fields.

`StageName` is the five names on the [stage map](../STAGE_MAP.md), not a
Delta table name. `SCOUT` is stage 1 Scout. `STAGE2`, `STAGE3`, and `STAGE4`
are stages 2, 3, and 4. `ORCH` is the orchestrator. No other name is in the
enum.

List fields take `first` and an optional opaque `after`. `first` is required
so a selection cannot mean "every row". Cursors are opaque. This draft does
not define how a cursor is encoded. There is no `doc(id:)` or `stage(id:)`
on `Query`. A document or a stage is reached only through a job the caller
may read.

Parent links on `Stage` and `Doc` are ids, not object fields. Selecting
`job { stages { job { stages } } }` is not expressible. The graph does not
cycle.

```graphql
# Draft SDL. Not loaded. Not served. Disabled by default.

"""Reads of gold aggregates the caller may see. Not implemented."""
type Query {
  """Null when the id is unknown or not granted. Those cases match."""
  job(id: ID!): Job

  """Granted jobs only. `first` is required and is not 'all jobs'."""
  jobs(first: Int!, after: String): JobConnection!
}

"""One crawl job's gold aggregate. Not a Delta table and not a queue row."""
type Job {
  """The crawl job id (`scrapy.crawl_job_id`), not a new id space."""
  id: ID!
  stages(first: Int!, after: String): StageConnection!
}

"""One pipeline stage of one job. `name` is not a table name."""
type Stage {
  id: ID!
  name: StageName!
  jobId: ID!
  docs(first: Int!, after: String): DocConnection!
}

"""One document aggregate under a stage. Not a raw page body."""
type Doc {
  id: ID!
  jobId: ID!
  stageId: ID!
}

enum StageName {
  SCOUT
  STAGE2
  STAGE3
  STAGE4
  ORCH
}

type PageInfo {
  hasNextPage: Boolean!
  endCursor: String
}

type JobConnection {
  edges: [JobEdge!]!
  pageInfo: PageInfo!
}

type JobEdge {
  cursor: String!
  node: Job!
}

type StageConnection {
  edges: [StageEdge!]!
  pageInfo: PageInfo!
}

type StageEdge {
  cursor: String!
  node: Stage!
}

type DocConnection {
  edges: [DocEdge!]!
  pageInfo: PageInfo!
}

type DocEdge {
  cursor: String!
  node: Doc!
}
```

The schema has no `Mutation` and no `Subscription`. A write, a crawl start,
a pause, or a cancel is not this API. The crawl job API
([#147](https://github.com/BenjaminSRussell/Scrapy/issues/147)) is a
different product. This draft does not satisfy it, and it does not satisfy
the Control Center API
([#148](https://github.com/BenjaminSRussell/Scrapy/issues/148)).

[#749](https://github.com/BenjaminSRussell/Scrapy/issues/749) says a REST
export may land first. This draft does not define that export and does not
add a REST route.

## Disabled by default

The read API stays off. This change does not mount a path, open a port, or
start a process. It does not choose a URL. It does not add a key to
`Scraping_project/config.yml`.
[ADR-0001](../adr/0001-config-yml-is-canonical.md) still says that file is
canonical configuration; this draft does not extend it.

Off means:

- No GraphQL endpoint is registered.
- The SDL is not parsed by a server in this repository.
- An unauthenticated caller and an authenticated caller get the same result
  from this change: there is no endpoint to call.
- A later enable switch, if some other change adds one, defaults to off.
  A missing switch is off. This draft does not name that switch and does
  not add it.

Enablement is not allowed by this document. A later change may turn the API
on only when both of the following are already true: auth is required, as
the next section says, and a complexity budget is in force, as the section
after that says. Turning the API on is a separate change. It is not implied
by merging this file.

## Auth required

Auth is required on every call. There is no anonymous query, no shared
default credential, and no bypass for an internal network or an "ops"
caller.

This draft does not choose a header, a token format, or an issuer. It does
not add a credential. The rules below are the contract a later
implementation has to meet. Until they are met, the API stays disabled.

1. **Authenticate first.** The principal is authenticated before any field
   resolver runs and before any lake path is opened. Failure rejects the
   whole operation. The response has no data. It does not echo the requested
   job id, and it does not say whether that id is known.
2. **Authorize the job.** A principal reads a job only when a grant names
   that principal (or a role the principal holds) and that job. Stages and
   docs under the job ride that same grant. They are not separately public.
3. **Missing and forbidden match.** `job(id)` is null when the job does not
   exist and when it exists but is not granted. `jobs` omits both. The
   connection does not include a count of jobs the caller cannot see.
4. **No existence oracle.** Before a grant exists, the API does not reveal
   whether a job id, a stage id, or a doc id is stored. Ids the caller is
   allowed to read may appear. Others are null or omitted.
5. **Gold only, once gold exists.** A grant for this API is a read of gold
   aggregates for granted jobs. It does not authorize bronze, silver,
   queues, quarantine, exports, raw bodies, or writes. It does not
   authorize today's stage tables. Those tables are not this schema.
6. **Org id is not a grant.** An `org_id` on a job, if
   [#637](https://github.com/BenjaminSRussell/Scrapy/issues/637) adds one,
   does not by itself authorize this read. Membership in an org is not a
   grant. The same rule is in the
   [federated gold-query stub](federated-tenant-gold-query.md).
7. **Read only.** Resolvers must not promote, delete, vacuum, export, or
   append. Time travel, if a later design adds it, stays inside the same
   grants and still returns only these fields.
8. **Grant changes apply to the next call.** This draft does not define how
   fast a revocation reaches a call that has already been authorized.

A tenant layout, if [#671](https://github.com/BenjaminSRussell/Scrapy/issues/671)
lands later, does not replace these grants. This draft does not count
tenants and does not add a tenant argument to `Query`. The federated stub
covers cross-tenant search. This schema does not.

## Complexity limit

A complexity limit is required before the API can be enabled. The limit is
structural. It is not a latency target, not a timeout, and not a tenant
cap. This draft does not pick the integers. An unset budget is not
unlimited. An unset budget means the API stays disabled and the operation
is rejected.

The check runs on the expanded operation, after fragments are inlined and
after aliases are kept as separate selections. It runs before any resolver
and before any lake read. Failure rejects the whole operation. There is
no partial `data`.

Rules:

1. **`first` is mandatory on every list.** The lists are `jobs`, `stages`,
   and `docs`. `first` below 1 is rejected. `first` above the server
   maximum is rejected. This draft does not choose that maximum. There is
   no default that means "all rows".
2. **Aliases count.** Two aliases of `stages` are two list selections, not
   one.
3. **Nested lists multiply.** Along a path, the cost is the product of each
   list field's `first`. A selection of `jobs` → `stages` → `docs` costs
   `jobs.first * stages.first * docs.first` for that path. Each aliased
   path adds its own product. `edges` is not a second multiplier; its
   length is the parent list's `first`.
4. **The sum of those path costs must be within the budget.** Over budget
   is a reject, not a truncated payload and not a slower query.
5. **Depth does not grow by walking parents.** `Stage` and `Doc` expose
   ids, not parent objects with list fields, so a selection cannot
   re-enter `stages` or `docs` through a back-edge. The depth cap is still
   required: a selection deeper than the schema's own tree (connection,
   edge, and node wrappers included) is rejected. Introspection fields, if
   a later server would expose them, count toward depth. This draft does
   not enable introspection.
6. **The limit is not an ACL.** A query under the budget from a caller
   with no grant is still denied. A granted caller over the budget is
   still rejected. The two errors are different. A complexity rejection
   does not say whether a job id exists.

Pagination cursors do not raise the budget. A client that needs more rows
sends another operation with `after`. Each operation is scored on its own.

## Out of scope

- Implementing a server, a resolver, a Python helper, a config flag, a Helm
  value, or a dashboard client.
- Adding strawberry, ariadne, or any other dependency.
- Choosing gold column names beyond the ids and the stage-name enum above.
- Choosing a complexity-budget integer, a maximum `first`, a timeout, or a
  latency target.
- Choosing a URL path, a port, or a token format.
- Serving this schema from the stage tables that exist today.
- The REST export the issue says may come first. This draft does not
  specify it.
- Cross-tenant search, which remains
  [#770](https://github.com/BenjaminSRussell/Scrapy/issues/770).

## Related

- [#749](https://github.com/BenjaminSRussell/Scrapy/issues/749) — this draft.
- [#303](https://github.com/BenjaminSRussell/Scrapy/issues/303) — bronze / silver / gold layering. These aggregates are undefined until that lands.
- [#770](https://github.com/BenjaminSRussell/Scrapy/issues/770) — federated query across tenant gold lakes. Separate product. Stub: [federated tenant gold query](federated-tenant-gold-query.md).
- [#147](https://github.com/BenjaminSRussell/Scrapy/issues/147) — crawl job API (create, pause, cancel). Not this read schema.
- [#148](https://github.com/BenjaminSRussell/Scrapy/issues/148) — API-backed Control Center. Not this read schema.
- [#637](https://github.com/BenjaminSRussell/Scrapy/issues/637) — `org_id` on jobs. Not an ACL grant.
- [#671](https://github.com/BenjaminSRussell/Scrapy/issues/671) — per-tenant Delta prefix. Not an ACL grant.
- [Data usage guide](../../Scraping_project/docs/guides/DATA_USAGE.md) — today's single lake root and stage tables.
- [Stage map](../STAGE_MAP.md) — the five stage names this enum copies.
- [Roadmap](../ROADMAP.md) — bronze/silver/gold is listed under later work.
- [ADR-0001](../adr/0001-config-yml-is-canonical.md) — canonical config. This draft adds no key.
