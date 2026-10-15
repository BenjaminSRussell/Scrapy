# Push summaries to Notion or Airtable (stub)

- **Status:** Stub. Experimental. Design only. Off by default. No client, config key, UI, or code path is added here.
- **Date:** 2026-10-15
- **Issue:** [#892](https://github.com/BenjaminSRussell/Scrapy/issues/892)

Issue #892 asked for summaries to be pushed to Notion or Airtable, with a settings panel, a stub push that shows a payload preview, and an experimental label. This file records that product shape. It does not implement any of it and does not change runtime behavior.

Nothing here sends data. The tree has no Notion or Airtable client, token, SDK pin, or config key, and this file does not add one.

## What exists today

- **No export webhook.** There is no outbound webhook client. The closest code is the Alertmanager stub in [anomaly_scan.py](../../Scraping_project/scripts/anomaly_scan.py). `print_webhook` prints the body and says it was not sent. Alertmanager's external receivers stay commented out until an operator enables them ([alertmanager-paging.md](../../Scraping_project/monitoring/alertmanager-paging.md)).
- **Local export only.** `LakehouseManager.export` writes local files through `_ExportSink` ([lakehouse_manager.py](../../Scraping_project/src/lakehouse/lakehouse_manager.py)).
- **Log and gauge only.** `GrafanaSummaryPipeline` ([pipelines.py](../../Scraping_project/src/pipelines.py)) joins sampled text, caps it at 2000 characters, sets a Prometheus gauge (scraped, not pushed), and logs a 200-character prefix. It makes no outbound call.
- **The entity summary is a placeholder.** `AggregationPipeline` is on by default: `AGGREGATION_ENABLED` defaults to true, and the pipeline is in `ITEM_PIPELINES` at 500 in [settings.py](../../Scraping_project/src/settings.py). It writes summary rows to the Delta table `entity_summaries`. `_generate_entity_summary` builds an LLM prompt, assigns it to `_`, and returns `Summary for <entity> based on N sources (most recent first)`. A push of that column today would send a templated sentence, not a summary.
- **Stage summary tables.** Stage 3 writes `stage4_summaries`, and Stage 4 writes `stage4_large_doc_summaries` ([STAGE_MAP](../STAGE_MAP.md)). The Stage 3 name split is open as #612.

## Connector settings UI (design only)

A connector panel would sit in the Control Center under `dashboard/`. This change does not edit `dashboard/`, so the panel is specified here and not built.

| Setting | Default | Rule |
|---|---|---|
| Target | `none` | `none`, `notion`, or `airtable`. No other value is accepted. |
| Push enabled | off | Boolean `true` only. The stub never sends, so this has no effect in the stub. |
| Credential reference | unset | Name of the environment variable that holds the token. The panel shows whether that variable is set, never its value. |
| Destination id | unset | A database or base id. There is no URL field. The host is fixed per target. |
| Summary source | `entity_summaries` | The only table specified. Other tables need their own design. |
| Preview | required | The preview below is shown before any push. |

The token never appears in the panel, in `config.yml`, in a preview, or in a log line. Create it outside this repo, as the paging runbook does for its routing keys.

## Stub push with payload preview (design only)

One function builds both the preview and the push body, so the preview cannot drift from what a real push would send.

- **Input:** a target and a list of summary records.
- **Output:** the records that would be sent, the record count, and the total byte size.
- **Stub behavior:** no socket, no token read, no retry. It returns the preview and nothing else.
- **Allow-list:** each record keeps only `entity_id`, `summary`, `source_count`, and `created_at`. Titles and `top_urls` are not sent. Each `summary` is capped at 2000 characters, the same cap `GrafanaSummaryPipeline` uses.

Example preview with placeholder values (not real data):

```json
{
  "target": "notion",
  "dry_run": true,
  "record_count": 1,
  "records": [
    {
      "entity_id": "example-entity-1",
      "summary": "Summary for example-entity-1 based on 3 sources (most recent first)",
      "source_count": 3,
      "created_at": "2026-10-15T00:00:00"
    }
  ]
}
```

This is the stub's own shape, not the Notion or Airtable wire format. Each vendor's request shape must be checked against its current API reference in the client change. This file does not claim it.

## Experimental

- Labelled experimental here, and in the panel once it exists.
- Off by default. The repo's precedent is an explicit opt-in that defaults to false: `ENABLE_EXPERIMENTAL_SPIDERS` in [settings.py](../../Scraping_project/src/settings.py), described in [src/stage1/experimental/README.md](../../Scraping_project/src/stage1/experimental/README.md). A push needs its own default-false opt-in, separate from the spider flag. The implementation issue names it.
- No production path. The production Helm overlay turns on default-deny NetworkPolicies, and Alertmanager's external receivers stay closed ([CHANGELOG.md](../../CHANGELOG.md), #190). A push needs its own egress rule in a later change.

## Data egress

A push copies text derived from scraped pages to a third party, and that cannot be undone once sent. So:

- The first real push needs an ADR in [docs/adr/](../adr/README.md), because it is hard to reverse.
- It also needs a data-handling review covering the source sites' terms and any personal data in the summaries.
- The preview is the operator's check on what leaves the host.
- Adding a field to the allow-list is a reviewed code change, not a setting.

## Not in this change

- No client, SDK, HTTP call, or requirement pin.
- No config key, settings change, pipeline wiring, dashboard UI, or Helm value.
- No test and no CHANGELOG line. There is no code, and no runtime behavior changes.

## Open for the implementation issue

1. Which target ships first, Notion or Airtable.
2. Real summary text. A push should wait until `_generate_entity_summary` returns a synthesized summary. If it goes out earlier, it must carry a marker that the text is a placeholder.
3. Repeat writes. An entity can be flushed more than once (see the spill and flush logic in `AggregationPipeline`), so `entity_id` alone may not be a unique key. The client must handle repeats.
4. The egress rule and the ADR from the data egress section.
