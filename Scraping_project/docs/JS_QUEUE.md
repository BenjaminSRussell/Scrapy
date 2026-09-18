# JS spider queue hop (Scout → javascript → Stage2)

## Hop contract

1. **Scout** discovers HTML URLs and, when `enable_js_spider` is true, yields
   queue items targeting the `javascript` spider.
2. **QueueItemPipeline** appends those items to Delta table `js_spider_queue`
   (`status: pending`).
3. **PipelineOrchestrator.run_full_pipeline** runs Scout (`run_stage1`), then
   **`run_js_queue()`**, which starts the Scrapy `javascript` spider against
   pending queue URLs.
4. The javascript spider marks drained URLs `completed` and may enqueue further
   discoveries for Stage2 / seeds.
5. **Stage2** then analyzes `stage2_queue` as usual.

If the JS path is disabled, Scout must not enqueue JS queue items (guardrail in
`scout_spider.py`), and `run_js_queue()` skips the crawl (warns if pending > 0).

## Feature flag

| Source | Key | Default |
|--------|-----|---------|
| Config | `stages.stage1.enable_js_spider` or `stage1.enable_js_spider` in `config.yml` | `true` |
| Env | `ENABLE_JS_SPIDER` (`0`/`1`/`true`/`false`/`yes`/`on`) | unset → use config |
| Arg | `PipelineOrchestrator.run_js_queue(enabled=...)` | overrides all |

## Metric

- **`pipeline_js_queue_pending`** (Prometheus Gauge) — pending count in
  `js_spider_queue`, updated when the orchestrator drains (or skips) the queue.
  Setter: `src.scrapy_prometheus.set_pipeline_js_queue_pending`.

## Ops notes

- Pending should trend toward 0 after a green full-pipeline run with JS enabled.
- Alert if `pipeline_js_queue_pending > 0` with no javascript spider activity.
- Full Playwright E2E fixture (AC1) is a follow-up; unit coverage lives in
  `tests/unit/test_js_queue_drain.py`.

## Verify

```bash
pytest tests/unit/test_js_queue_drain.py -v -o addopts=
```
