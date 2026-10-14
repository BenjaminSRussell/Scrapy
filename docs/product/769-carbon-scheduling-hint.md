# Carbon-aware scheduling hint for Stage 3 and Stage 4

- **Status:** Stub only. Optional. Default off. Workers do not read it.
- **Date:** 2026-10-14
- **Issue:** [#769](https://github.com/BenjaminSRussell/Scrapy/issues/769)

## Config

`carbon_scheduling` in [config.yml](../../Scraping_project/config.yml) is the whole switch.

| Key | Default | Meaning |
|---|---|---|
| `carbon_scheduling.enabled` | `false` | Hint on only when this is the boolean `true`. `1`, `"true"`, `"yes"`, and `"on"` leave it off. |
| `carbon_scheduling.delay_window_minutes` | null | Optional delay window in minutes. Not applied. |

The delay window is optional. Leaving it null, or omitting it, does not change Stage 3 or Stage 4.

## Carbon reading

The reading is a local stub: [`carbon_hint.py`](../../Scraping_project/src/stage3/carbon_hint.py) returns the fixed string `local-stub`. That string is not a grid intensity. The function does not open a socket.

This stub does not call a public carbon API. It does not add an HTTP client, an API key, or a dependency. Do not describe `local-stub` as a live carbon reading.

## Ethics and ops

Do not move a batch because of a number that was not measured. A stub used as if it were grid carbon would delay or advance work for a reason the value does not support, and a failed lookup could be mistaken for a reason to wait.

Stage 3 and Stage 4 workers do not consult this hint. They do not delay, skip, or reschedule a batch when the config is missing or the stub is unread. A missing carbon reading must not block a batch. Leave `enabled` at `false` until a later change can fail open on a real, reviewed signal. This file does not add that signal.

No SLO, alert, dashboard series, or metric is added here.
