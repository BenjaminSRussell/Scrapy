# Discovery error codes (rustmapper and Stage 1)

Stringly discovery errors cannot be routed. rustmapper (when it writes a
handoff) and Stage 1 (`SitemapParser`, `consume_rustmapper_handoff`) share
one field, `error_code`, on the discovery handoff JSON. The human string
stays in `error`.

`Rust-sitemap/src/sitemap_seeder.rs` and `Rust-sitemap/src/network.rs` are
not in this repository. Those producers must emit the codes below. Stage 1
is the consumer and classifies its own sitemap walk into the same set.

A missing sitemap (HTTP 404, or no document at all) is not a code. That
handoff stays `empty`. Empty is not a failure and is not queued for retry.

## Codes

| `error_code` | Handoff status | Meaning |
|---|---|---|
| `OK` | `success` | Walk finished with URLs and no coded failure |
| `PARTIAL` | `success` | At least one URL, and a cap or a coded failure on another document |
| `TIMEOUT` | `failed` | Deadline or a dropped connection |
| `ROBOTS_DENY` | `failed` | robots.txt disallow |
| `HTTP_429` | `failed` | Rate limited |
| `PARSE_FAIL` | `failed` | Sitemap XML, gzip, or JSONL could not be parsed |

Wire values are the uppercase names. Readers also accept the same spelling
in any case. Any other string is ignored (`error_code` null) and is not retried.

When several failure codes apply, Stage 1 keeps one: `ROBOTS_DENY`, then
`HTTP_429`, then `TIMEOUT`, then `PARSE_FAIL`. A robots deny is never
retried just because another document also timed out.

## Retry policy table

Stage 1 maps each code through `RETRY_POLICY_TABLE` in
`Scraping_project/src/stage1/discovery_errors.py`
(`stage1_retry_plan`). Queue rows use the shared `RetryPolicy` in
`Scraping_project/src/utils/retry.py`: `queue_attempts`, `backoff_base`,
`backoff_max`, and `retryable_statuses` (#215). 429 stays retryable and is
not an in-request retry (#582).

| `error_code` | Action | Retry policy |
|---|---|---|
| `OK` | `none` | no retry |
| `PARTIAL` | `ingest_partial` | ingest the URLs already discovered; do not requeue the discovery job |
| `TIMEOUT` | `queue` | `RetryPolicy.queue_attempts`, `backoff_base`, `backoff_max`; in-request when 408 or 504 is in `in_request_statuses` |
| `HTTP_429` | `queue` | `RetryPolicy.queue_attempts`, `backoff_base`, `backoff_max`; 429 stays in `retryable_statuses` and out of `in_request_statuses` |
| `ROBOTS_DENY` | `drop` | no retry |
| `PARSE_FAIL` | `drop` | no retry |

Scout logs `retry_action` and still does not loop the fetch. The orchestrator
owns the queue retry. Re-walking inside the sitemap parser would multiply
live requests.

## Fixtures

One JSON handoff per code lives in
`Scraping_project/tests/stage1/fixtures/discovery_errors/`. Each file names
the code and the retry action from the table above.
