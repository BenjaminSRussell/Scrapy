# Runbook: Stage 2 hop reconciliation and the Stage 2 → 3/4 barrier

Issue: #646.

After Stage 2, `run_full_pipeline` compares `stage2_queue` (per URL) with the
snapshot taken when Stage 2 started:

| Hop | Meaning |
|-----|---------|
| `enqueued` | rows `pending` when Stage 2 started |
| `claimed` | of those, no longer `pending` |
| `ok` / `failed` | claimed rows now `completed` / another terminal status |
| `lost` | claimed rows gone from the queue (silent loss) |
| `still_pending` | rows Stage 2 left `pending` (deferred, retrying, or a status write that failed) |
| `late_appends` | `pending` rows that appeared after Stage 2 started; the next run picks them up |

The result is `PipelineStats.hop_funnel` (`panel: hop_funnel`, with alerts and
`stage2_watermark`) and the counter `pipeline_hop_alerts_total{alert}` with
`alert` = `hop_lost` | `stage2_pending` | `late_append`.

## Success rule

`claimed` must equal `ok + failed` within `hop_tolerance`. Otherwise
`stage_errors["reconciliation"]` is set and a run that would be `complete` is
`partial_failed`, which raises `PipelineRunError` unless `allow_partial=True`
(the #521 rule).

## Switches

Env vars override the orchestrator `config` dict keys.

| Env | config key | Default | Effect |
|-----|-----------|---------|--------|
| `STAGE2_BARRIER` | `stage2_barrier` | `flag` | `flag`: alert on pending rows and start Stage 3/4. `strict`: refuse Stage 3/4 while any `stage2_queue` row is pending (run `failed`, `stage_errors["stage2_barrier"]`). `off`: no accounting. |
| `HOP_TOLERANCE` | `hop_tolerance` | `0` | rows allowed to vanish before reconciliation fails (e.g. for a shared queue) |
| `STAGE3_4_PARALLEL` | `stage3_4_parallel` | `true` | `false` runs Stage 3, then Stage 4 (Stage 4 still runs if Stage 3 fails) |

Stage 3 and Stage 4 start only after the barrier sets the watermark, so they no
longer race Stage 2. They read different tables, which is why parallel stays
the default (the #684 cancellation semantics depend on it).

## Triage

- **`hop_lost`:** rows vanished from `stage2_queue` during Stage 2. Look for a
  concurrent writer overwriting the table (another worker, a GC/compaction job)
  in the same window. Raise `HOP_TOLERANCE` only for queues that are shared on
  purpose.
- **`stage2_pending` on every run:** check Stage 2 logs for `Analysis upsert
  failed; leaving N URLs pending` or `stage2_queue_update_failures_total`.
- **`late_append`:** expected when Scout or the JS drain overlaps Stage 2. Use
  `STAGE2_BARRIER=strict` for batch runs that must not leave work behind.
