# ADR-0002: Edge fetch workers post to Kafka

- **Status:** Proposed
- **Date:** 2026-10-14
- **Issue / PR:** #763

## Context

Stage 1 fetch runs inside the cluster, so every request leaves from one IP
space. [#763](https://github.com/BenjaminSRussell/Scrapy/issues/763) asks for
origin diversity and geo coverage. The approach it accepts is a design record
only: remote fetch workers (Cloudflare Workers or AWS Lambda) that post
results to Kafka. No fetcher is added here.

Today the in-cluster spider publishes scraped items through
[`KafkaPipeline`](../../Scraping_project/src/pipelines.py). The topic is
`kafka.topics.scraped_items` (`scraped-items`) in
[`Scraping_project/config.yml`](../../Scraping_project/config.yml), bridged to
`KAFKA_TOPIC` in [`src/settings.py`](../../Scraping_project/src/settings.py).
Records are JSON, keyed by `url_hash`
([`src/utils/kafka_config.py`](../../Scraping_project/src/utils/kafka_config.py)).
Downstream delivery is at-least-once; consumers dedupe on `url_hash`. Kafka is
not part of the main compose stack
([stage map](../STAGE_MAP.md)). Secrets stay out of `config.yml`
([ADR-0001](0001-config-yml-is-canonical.md),
[security policy](../../SECURITY.md)).

No edge worker exists. Nothing in this ADR was measured. No throughput,
latency, or cost figure is claimed.

## Decision

We will treat a future edge fetch worker as an untrusted Kafka producer and
nothing else. It fetches only URLs the trusted cluster assigned to it, and it
posts the result to the existing `scraped-items` topic. It does not crawl a
frontier of its own, and it does not write Redis, Delta, or Postgres.

This ADR does not choose Cloudflare Workers over Lambda, and it does not
authorize building or deploying either one. The in-cluster spider remains the
only fetch path.

### In scope

- The producer role: a remote worker posts one fetch result per assigned URL
  to `scraped-items`, as JSON keyed by `url_hash`, the same contract
  `KafkaPipeline` already uses. No new topic.
- Naming the two candidate runtimes (Cloudflare Workers, AWS Lambda) without
  picking one.
- The threat-model sketch below, which later implementation work has to
  satisfy.

### Out of scope

- Implementing a worker, a fetcher, a Python module, IaC, or a deploy.
- Editing the orchestrator, CLI, `config.yml`, compose, Helm values,
  dashboard, requirements, or `Cargo.lock`.
- Replacing in-cluster fetch, including the optional proxy-rotation
  middleware.
- JS or browser rendering at the edge.
- Giving the worker Redis, Delta, Postgres, consumer-group, or cluster-admin
  credentials.
- New topics, broker tuning, ACLs, and any production rollout.
- Throughput or capacity numbers. None have been measured.

## Threat model

Sketch only. These are constraints on a later design, not controls that exist
today.

**Assets.** The `scraped-items` stream and anything that consumes it; Kafka
credentials; URLs and response bodies (untrusted page content); cluster data
stores the worker must not reach.

**Trust boundary.** The worker runs outside the cluster, on a third-party
platform, and handles attacker-controlled HTML. Compromise of the worker, of
its supply chain, or of a page it fetches is in scope. Cluster-admin
compromise is not a starting assumption.

### Untrusted worker

A worker can emit, omit, replay, or poison records. Deduping on `url_hash`
does not make the body trustworthy. Downstream already has to treat the topic
as untrusted input (schema checks and the validation-failures topic stay
where they are).

Constraints for any later build:

- The worker is produce-only on `scraped-items`. No consume, no topic admin,
  no other topic.
- The cluster assigns the URL list. The worker does not discover URLs and
  does not follow a redirect or a link to an address it was not given,
  including link-local and cloud-metadata addresses.
- The worker's only data-plane peer is the Kafka bootstrap. It has no path to
  Redis, Postgres, Delta, or the orchestrator.
- Each record carries a worker identity so one bad producer can be quarantined
  without trusting the body.
- Page bytes go to Kafka only. The worker does not POST them anywhere else.

### Credential handling

Follow [SECURITY.md](../../SECURITY.md) and
[ADR-0001](0001-config-yml-is-canonical.md): no secrets in source, `config.yml`,
Helm values, logs, or issues.

- The worker does not receive the cluster producer password, or any Redis,
  Postgres, or object-store credential.
- Its own produce credential is separate, scoped to `scraped-items`, and
  stored in the platform secret store (Workers secrets or the Lambda
  environment's secret manager). It is not baked into a script or a zip.
- Prefer a short-lived token minted by the trusted side. Rotating it must not
  rotate cluster credentials.
- Do not log the credential, request `Authorization` headers, or response
  bodies.
- Local Kafka (`localhost:9092`, no SASL) is not an acceptable endpoint for
  an edge worker. A worker requires an authenticated broker.

### No production deploy

This ADR deploys nothing. It does not create a Cloudflare Worker, a Lambda
function, an IAM role, a topic ACL, a broker listener, or a Helm change, and
it does not start a scrape.

Shipping a worker, pointing one at a broker, or calling this path production
requires a later ADR that supersedes this one after the constraints above are
actually in place. Until then, edge fetch is a written decision only.

## Consequences

- Fetch diversity stays a design, not a runtime. Reviews of #763 can stop at
  this file.
- The trust boundary is explicit: an edge worker is a hostile producer with a
  single topic credential.
- Easy to reverse: delete or supersede this ADR. Nothing is deployed.
- Revisit when an implementation is proposed, when the `scraped-items`
  contract changes, or when production use is requested. That revisit is a
  new ADR, not an edit that silently authorizes a deploy.

## Alternatives considered

- **Keep all fetch in the cluster.** This is still the only implemented path.
  It does not give another origin or geo, which is the problem #763 records.
- **In-cluster proxy rotation**
  ([`proxy_rotation_middleware.py`](../../Scraping_project/src/stage1/middlewares/proxy_rotation_middleware.py)).
  Stays inside the current trust boundary and does not add a remote producer.
  Left as-is; this ADR does not enable or disable it.
- **Workers write Delta, Redis, or call the orchestrator directly.** A wider
  trust boundary for the same fetch. Rejected.
- **A new Kafka topic for edge results.** Splits the contract and needs a new
  consumer. Rejected; reuse `scraped-items`.
