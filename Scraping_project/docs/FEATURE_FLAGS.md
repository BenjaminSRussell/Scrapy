# Feature flags and kill-switches (#304)

`src/utils/feature_flags.py` is the one way to read on/off switches:

```python
from src.utils.feature_flags import get_bool, get_int, get_str

if get_bool("ENABLE_EXPERIMENTAL_SPIDERS", False):
    ...
```

**Lookup order:**

1. The environment variable (`FLAG_NAME`).
2. An optional `feature_flags:` section in `config.yml`, keyed by the lowercase name (`enable_experimental_spiders: true`).
3. The default given at the call site.

**Booleans** accept `1/true/yes/on` and `0/false/no/off`, case-insensitive. Any other value logs a warning and falls back to the default, so a typo never silently flips a kill-switch.

New experimental features must **default to off**. Add each new flag to `FLAGS` in the module and to the table below.

| Flag | Default | Used by | Purpose |
|---|---|---|---|
| `ENABLE_EXPERIMENTAL_SPIDERS` | off | `src/settings.py`, `src/stage1/experimental/gate.py`, `cli.py` | Allow the lab spiders `javascript`, `deep_dive` and `depth` to crawl (#391/#442) |
| `SSRF_GUARD_ENABLED` | on | `src/settings.py` | Kill-switch for the SSRF download guard (#682); keep it on in production |
| `ASR_ENABLED` | off (or `scrapy.asr_enabled`) | `src/settings.py` | Register the speech-to-text pipeline (#470) |
| `STAGE4_ASR_ENABLED` | off | `src/stage4/summarization.py`, `src/stage4/stage4_worker.py` | Fold local audio/text fixtures into Stage 4 summaries (#1101). Does not download remote media |
| `KAFKA_DLQ_ENABLED` | on | `src/pipelines.py` (`KafkaPipeline`) | Write undeliverable Kafka items to the file DLQ (#162) |
| `STAGE2_CONTENT_CACHE_ENABLED` | off | `src/stage2/stage2_worker.py`, `src/utils/cache.py` | Reuse a Stage 2 HTML body keyed by url_hash plus ETag or Last-Modified. A hit skips the network fetch and increments `cache_hit_total` (#478). No validator means the response is not cached |
| `PEOPLE_DIRECTORY_PARSE_ENABLED` | off | `src/stage2/intelligent_analyzer.py`, `src/stage2/people_directory.py` | Emit high-confidence faculty/people `PersonItem` rows (`name`, `email`, `title`) from a short directory card. Prose and generic mailboxes are skipped (#642) |

`feature_flags.snapshot()` returns the effective value of every registered flag, which is useful when debugging a deployment.
