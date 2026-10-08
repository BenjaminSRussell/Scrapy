# Runbook: `js_spider_queue` (Scout → javascript spider → Stage 2)

Issue: #645.

## Hop contract

1. **Scout** yields every followed HTML link to `stage2_queue` and, when the JS
   path is on, also to `js_spider_queue` (`target_spider: javascript`).
2. **QueueItemPipeline** writes those rows to Delta (`status` missing or `pending`).
3. **`PipelineOrchestrator.run_full_pipeline`** runs Scout, then **Stage 1b**
   (`run_js_queue()`): when rows are pending it runs `scrapy crawl javascript`
   in a child process (Stage 1 already used this process's Twisted reactor,
   which cannot be restarted), then Stage 2 → Stage 3 ∥ Stage 4.
4. The **javascript spider** renders each pending URL with Playwright, sends the
   links it finds to `seed_urls` **and `stage2_queue`**, and on close marks rows
   `completed` or `failed` (with `error`, `failed_at`). Nothing it attempted
   stays `pending`.

A drain problem (non-zero exit, timeout, Playwright missing) is logged and
recorded in `PipelineStats.stage1_js_drain_error`; it does not fail the run,
because Stage 2 already has Scout's queue.

## Switch

| Source | Key | Default |
|--------|-----|---------|
| Argument | `run_js_queue(enabled=...)` / orchestrator `config["enable_js_spider"]` | — |
| Env | `ENABLE_JS_SPIDER` (`0/1/true/false/yes/on`; blank ignored) | unset |
| config.yml | `stage1.enable_js_spider` (or `stages.stage1.enable_js_spider`) | `true` |

Precedence is top to bottom. When off, **Scout does not write
`js_spider_queue`** and Stage 1b only reports the pending count.

Drain timeout: `JS_DRAIN_TIMEOUT_SECONDS` (or orchestrator
`config["js_drain_timeout_seconds"]`), default 3600.

## Metric and alert

- `pipeline_js_queue_pending` (gauge): pending rows before and after Stage 1b.
- After a green full run with the JS path on it should return to 0. Alert when
  it stays above 0 across two runs, or when it is above 0 while
  `ENABLE_JS_SPIDER=0` (rows queued before the switch was turned off).

## Triage

- **Pending stays > 0:** check the Stage 1b log for `javascript spider exited N`
  or `timed out`; run `scrapy crawl javascript` from `Scraping_project/` to see
  the error (usually Playwright browsers not installed:
  `playwright install chromium`).
- **Many `failed` rows:** query `js_spider_queue` for `status = 'failed'` and
  group by `error`. To retry, set those rows back to `pending`.
- **Queue growing with JS off:** rows were written before the switch flipped;
  drain once with `ENABLE_JS_SPIDER=1`, or mark them `failed`.

## Known gap

Stage 2 still fetches each URL itself over plain HTTP, so a page whose content
exists **only** after JavaScript runs is analysed from its static shell. The
links the JS spider discovers on it do reach Stage 2. Analysing rendered HTML
in Stage 2 is tracked on #645.
