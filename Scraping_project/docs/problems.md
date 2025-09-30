# Project Issues & Roadmap

This document provides a comprehensive, prioritized list of all known issues, technical debt, and future improvements for the UConn Web Scraping Pipeline. It is intended to be the single source of truth for project planning and contribution guidance.

## Top 30 Prioritized Issues & Improvements

The following items are ranked by importance, considering their impact on functionality, stability, scalability, and maintainability. I have removed the "Future Features" section to focus exclusively on existing bugs and technical debt.

### Category: 🔴 Critical Bugs & Blockers

*These issues prevent core functionality from working as intended and must be addressed first.*

| Rank | ID | Issue | Impact | Location(s) |
|---|---|---|---|---|
| 1 | `BUG-001` | **Stage 3 Orchestrator `NameError`** | The `--stage 3` and `--stage all` CLI flags fail due to a `NameError` (`urls_for_enrichment`). This breaks the end-to-end pipeline. | `src/orchestrator/pipeline.py` |
| 2 | `BUG-002` | **Brittle Stage-Level Error Handling** | A single network error (e.g., timeout, DNS failure) can crash an entire stage instead of gracefully skipping the failed URL and continuing. This is especially true in Scrapy spiders where a single unhandled exception can stop the crawl. | `src/stage2/validator.py`, Scrapy Spiders |
| 3 | `BUG-003` | **Unsafe Subprocess CWD** | The Stage 3 subprocess call relies on a calculated `cwd`. If the project is run from a different directory structure (e.g., as an installed package), this path will be incorrect, causing the Scrapy command to fail. | `src/orchestrator/pipeline.py` |
| 4 | `BUG-004` | **Unclean Scrapy Shutdown** | In `run_stage1_discovery`, if `process.start()` raises an exception, `process.stop()` is never called, potentially leaving resources hanging. | `src/orchestrator/main.py` |
| 5 | `BUG-005` | **Inconsistent `main.py` Entrypoints** | The project has two `main.py` files (`main.py` and `src/orchestrator/main.py`) with overlapping responsibilities. This creates confusion about the true entrypoint and leads to brittle `sys.path` manipulation. | `main.py`, `src/orchestrator/main.py` |

### Category: 🟡 Scalability & Performance

*These issues limit the pipeline's ability to handle large-scale crawls and operate efficiently.*

| Rank | ID | Issue | Impact | Location(s) |
|---|---|---|---|---|
| 6 | `SCALE-001` | **Inefficient In-Memory Deduplication** | Stage 1's in-memory URL deduplication consumes excessive RAM on large crawls and does not persist, causing redundant work on restarts. | `src/stage1/discovery_spider.py` |
| 7 | `SCALE-002` | **Lack of Persistent State & Checkpoints** | The pipeline cannot be resumed from a point of failure. A crash requires restarting the entire process from scratch, which is highly inefficient. | `src/orchestrator/main.py`, Stage modules |
| 8 | `SCALE-003` | **Stage 3 Orchestration via Subprocess** | Running Stage 3 via `subprocess.run` is a fragile workaround for Scrapy/asyncio reactor conflicts. It's slow, hard to debug, and passing data via temporary files is not scalable or robust. | `src/orchestrator/pipeline.py` |
| 9 | `SCALE-004` | **Incomplete Dynamic Discovery Throttling** | The heuristics for finding dynamic/AJAX URLs can produce noisy or irrelevant links, and the mechanism to throttle them is incomplete. | `src/stage1/discovery_spider.py` |
| 10 | `SCALE-005` | **Blocking File I/O in Async Code** | Synchronous file I/O (`open`, `json.dump`) is used within `async` functions (e.g., `load_stage1_results`, `run_concurrent_stage3_enrichment`). This blocks the event loop, negating the benefits of `asyncio` and hurting performance under load. | `src/orchestrator/pipeline.py`, `src/common/storage.py` |
| 11 | `SCALE-006` | **Unbounded In-Memory Data Collection** | In `run_concurrent_stage3_enrichment`, `validation_items_for_enrichment` collects all URLs in memory before starting the Scrapy process. This will cause an `OutOfMemoryError` on large datasets. | `src/orchestrator/pipeline.py` |
| 12 | `SCALE-007` | **Fixed Batch Sizes** | The `BatchQueue` in the orchestrator uses a fixed batch size. This is inefficient, as optimal batch sizes can vary depending on network latency and processing time for different stages. | `src/orchestrator/pipeline.py` |

### Category: 🔵 Technical Debt & Maintainability

*These items increase the complexity of the codebase, making it harder to maintain, debug, and extend.*

| Rank | ID | Issue | Impact | Location(s) |
|---|---|---|---|---|
| 13 | `TECH-001` | **Overlapping & Complex Configuration** | The configuration system is confusing, with settings spread across YAML files, environment variables, and Scrapy settings, with unclear precedence. | `config/`, `src/orchestrator/config.py` |
| 14 | `TECH-002` | **Unstructured Logging** | Logs are simple text strings, not a structured format like JSON. This makes automated parsing, monitoring, and alerting difficult. | `src/common/logging.py`, All modules |
| 15 | `TECH-003` | **Inconsistent Professionalism in Code Comments** | Some comments are sarcastic or unprofessional, which reduces code clarity and maintainability. | `src/orchestrator/main.py`, `src/orchestrator/config.py` |
| 16 | `TECH-004` | **Potential Error Masking** | Some `try...except Exception` blocks are too broad, potentially hiding important errors that should be surfaced and handled explicitly. | `src/common/nlp.py`, `src/orchestrator/pipeline.py` |
| 17 | `TECH-005` | **Inconsistent Data Path Management** | There is a need to consolidate all data artifacts to ensure they are written under the main `data/` directory for consistency. | `src/orchestrator/config.py` |
| 18 | `TECH-006` | **Lack of Schema Versioning** | Output data schemas can change without a versioning system, which can break downstream consumers of the data. | `src/common/schemas.py`, Output pipelines |
| 19 | `TECH-007` | **Fragile `sys.path` Manipulation** | The entrypoint (`main.py`) modifies `sys.path` to make imports work. This is brittle and will break if the project is installed as a package or the directory structure changes. | `main.py`, `src/orchestrator/main.py` |
| 20 | `TECH-008` | **Optional Dependency Handling** | NLP dependencies (`spacy`, `transformers`, `torch`) are handled with `try...except ImportError`. This can lead to runtime failures if a feature is used without the necessary packages installed. A clear "extras" system in `setup.py` is needed. | `src/common/nlp.py`, `requirements.txt` |
| 21 | `TECH-009` | **Hardcoded Scrapy Spider Names** | The orchestrator hardcodes spider names like `'enrichment'`. If a spider name is changed, the orchestrator will break silently. | `src/orchestrator/pipeline.py` |
| 22 | `TECH-010` | **Monkey-Patching for Tests** | The project monkey-patches `Scrapy.Response.meta` in `src/common/__init__.py` to support tests. This is a global side effect that can lead to unpredictable behavior and makes testing less isolated. | `src/common/__init__.py` |
| 23 | `TECH-011` | **Inconsistent Async/Sync Usage** | The `URLCache` in `storage.py` uses synchronous `sqlite3` calls. When used in an async context, these block the event loop. The library `aiosqlite` should be used instead for true async database access. | `src/common/storage.py` |
| 24 | `TECH-012` | **Permissive SSL/TLS Verification** | The `SmartRequestHandler` creates a permissive SSL context that disables hostname checks and certificate verification. This is a security risk and should be configurable, defaulting to strict validation. | `src/common/request_infrastructure.py` |
| 25 | `TECH-013` | **Global State Objects** | The project uses global instances for `MetricsCollector` and `ErrorTracker`. This makes testing difficult as state can leak between tests and prevents running multiple pipelines in the same process. | `src/common/metrics.py`, `src/common/error_handling.py` |
| 26 | `TECH-014` | **Redundant Code in Data Refresh** | The `DataRefreshManager` has nearly identical logic in `refresh_validation_data` and `refresh_enrichment_data`. This code should be refactored into a single, reusable method. | `src/orchestrator/data_refresh.py` |
| 27 | `TECH-015` | **Lack of a Proper Packaging Setup** | The project lacks a `setup.py` or `pyproject.toml` file. This prevents it from being installed as a package, which is the standard solution for `sys.path` issues (`TECH-007`) and optional dependencies (`TECH-008`). | Root directory |
| 28 | `TECH-016` | **Inconsistent Documentation** | There are contradictions across documentation files (e.g., `code_reference.md` claims Stage 3 concurrency is "organized" while `problems.md` calls it "fragile"). This makes it hard for new contributors to understand the true state of the project. | `docs/` |

### Category: 🔵 Technical Debt & Maintainability (Continued)

| Rank | ID | Issue | Impact | Location(s) |
|---|---|---|---|---|
| 29 | `TECH-017` | **Fragmented Execution Model** | The project can be run in multiple ways (`python main.py`, `scrapy crawl`, `python -m src.stage...`), with different behaviors and configurations. This creates confusion and makes a single, reliable entrypoint difficult to establish. | `README.md`, `docs/code_reference.md` |
| 30 | `TECH-018` | **Manual Post-Installation Steps** | Setup requires manual steps after `pip install` (e.g., `python -m spacy download en_core_web_sm`). This complicates automated deployments and onboarding, and should be handled by a post-install script or clearer instructions. | `README.md`, `requirements.txt` |
| 31 | `TECH-019` | **Lack of Configuration Schema Validation** | The system loads YAML configuration without validating its structure or data types. A missing key or incorrect value (e.g., a string instead of an integer) can lead to subtle runtime errors instead of a clear failure at startup. | `src/orchestrator/config.py` |
| 32 | `TECH-020` | **Implicit Scrapy Project Root** | Scrapy commands (`scrapy crawl`) must be run from the project's root directory to find `scrapy.cfg`. This dependency on the current working directory is brittle and fails when run from other locations. | `scrapy.cfg`, `src/settings.py` |

### Category: 🧪 Recommended Tests

*Adding these tests would improve stability, prevent regressions, and validate key functionality.*

| Rank | ID | Test To Add | Reason | Location(s) |
|---|---|---|---|---|
| 29 | `TEST-001` | **End-to-End Orchestrator Test (`--stage all`)** | A full integration test that runs the entire pipeline via the orchestrator to confirm data flows correctly between all stages once `BUG-001` is fixed. | `tests/integration/` |
| 30 | `TEST-002` | **Large-Scale Memory Test** | A test that runs the pipeline with a very large number of URLs (e.g., >100k) to validate memory usage and performance, specifically to trigger `SCALE-001` and `SCALE-006`. | `tests/performance/` |
| 31 | `TEST-003` | **Configuration Precedence Test** | A test to verify that configuration settings from environment variables correctly override YAML files, and YAML files override Scrapy defaults. | `tests/orchestrator/` |
| 32 | `TEST-004` | **Graceful Failure/Skip Test** | A regression test that simulates network failures (e.g., using a mock server) and asserts that a stage continues processing other URLs instead of crashing. | `tests/stage2/`, `tests/spiders/` |
| 33 | `TEST-005` | **Dependency Installation Test** | A test suite that runs with minimal dependencies installed (no "extras") to ensure the application provides clear errors or gracefully degrades, rather than crashing with an `ImportError`. | `tests/installation/` |
| 34 | `TEST-006` | **Async Event Loop Blocking Test** | A test using `aiomonitor` or similar tools to detect if any synchronous calls are blocking the asyncio event loop for an unacceptable amount of time, targeting `SCALE-005` and `TECH-011`. | `tests/performance/` |
| 35 | `TEST-007` | **Packaging and Installation Test** | A test that builds and installs the project as a package (e.g., `pip install .`) and runs the entrypoint to ensure it works outside the development directory structure, which would validate fixes for `TECH-007` and `BUG-005`. | `tests/installation/` |
| 36 | `TEST-008` | **Configuration Schema Validation Test** | A test that attempts to run the orchestrator with a deliberately malformed configuration file (e.g., missing keys, wrong data types) and asserts that the application fails fast with a clear error message. | `tests/orchestrator/` |
