# Recorded stage hand-off fixtures (#686)

One JSONL file per boundary contract in `src/core/contracts.py`
(`stage1_stage2`, `stage2_stage3`, `stage2_stage4`, `stage4_chunk`). Each line:

```json
{"case": "...", "expect": "valid" | "reject", "error": "substring of the error", "record": {...}}
```

- `legacy_*` cases have no `schema_version` (rows written before versioning) and must stay readable.
- `future_version` cases come from a newer producer and must be rejected before any work.
- Extra unknown fields are allowed.

Replayed offline (no network, Kafka, Redis or lake service) by
`tests/contract/test_handoff_contracts.py`, against the contracts and the real
Stage 2/3/4 consumers.
