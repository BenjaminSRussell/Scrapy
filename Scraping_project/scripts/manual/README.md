# Manual / operator scripts

These are **not** part of the pytest suite. They may invent numbers, talk to a
live stack, or require interactive confirmation.

| Script | What it does |
|---|---|
| `benchmark_pipeline_10k_sim.py` | Simulated 10K-URL stage timings (random rates). Not a pass/fail gate. |

Prefer ``pytest -m smoke`` for a fast offline gate and ``make test-perf`` for
real load tests.
