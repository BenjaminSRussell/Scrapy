1️⃣ Orchestrator & Entry-Point Stability

ORG-001 – Stage 3 Orchestrator NameError — src/orchestrator/pipeline.py

ORG-002 – Brittle Stage-Level Error Handling — src/stage2/validator.py, Scrapy Spiders

ORG-003 – Unsafe Subprocess CWD — src/orchestrator/pipeline.py

ORG-004 – Unclean Scrapy Shutdown — src/orchestrator/main.py

ORG-005 – Blocking Scrapy Process in Async Orchestrator — src/orchestrator/main.py

ORG-006 – Missing Graceful Shutdown Signal Handling — src/orchestrator/main.py (inferred)

ORG-007 – Lack of Formal Packaging — README.md

2️⃣ Data Flow & Memory Management

ORG-008 – Incomplete Dynamic Discovery Throttling — src/stage1/discovery_spider.py

ORG-009 – Hardcoded Discovery Heuristics — src/stage1/discovery_spider.py

ORG-010 – Inflexible Seed Input — src/stage1/discovery_spider.py

ORG-011 – Lack of Persistent Deduplication — README.md, docs/stage1_master_plan.md

ORG-012 – Missing data/temp Directory Cleanup — docs/code_reference.md

ORG-013 – Potential for Stale Checkpoints — docs/stage1_master_plan.md

ORG-014 – Lack of Idempotency Guarantees — docs/stage1_master_plan.md

ORG-015 – Lack of Detail on BatchQueue Deadlock Prevention — docs/code_reference.md

ORG-039 – Inefficient Full Read of JSONL for Checkpointing — src/stage2/validator.py

ORG-040 – No Backpressure Handling for Queues — src/orchestrator/pipeline.py

ORG-041 – Data Loss on Unclean Shutdown — All stages

ORG-042 – In-Memory URL Canonicalization Cache Is Not Bounded — src/stage1/discovery_spider.py

ORG-043 – Stage 2 Validator Does Not Stream Output — src/stage2/validator.py

3️⃣ Configuration & Schema

ORG-016 – Unstructured Logging — src/common/logging.py, All modules

ORG-017 – Potential Error Masking — src/common/nlp.py, src/orchestrator/pipeline.py

ORG-018 – Lack of Schema Versioning — src/common/schemas.py, Output pipelines

ORG-019 – Lack of Configuration Schema Validation — src/orchestrator/config.py

ORG-020 – Unclear NLP Model Defaults — src/common/nlp.py (inferred from docs)

ORG-021 – Unimplemented Configuration Setting — docs/code_reference.md, docs/stage1_master_plan.md

ORG-022 – Inconsistent Schema Field Naming — README.md

ORG-023 – Lack of Configuration for NLP Fallbacks — docs/code_reference.md

ORG-044 – Overlapping Scrapy and Orchestrator Configurations — config/, src/settings.py

ORG-045 – No Centralized Schema Definition Source — src/common/schemas.py

ORG-046 – Magic Strings Used for Configuration Keys — All modules

ORG-047 – Inconsistent Naming for Output Files — config/development.yml

ORG-048 – Missing Scrapy Settings in YAML Configuration — config/development.yml

4️⃣ Security & Dependencies

ORG-024 – Optional Dependency Handling — src/common/nlp.py, requirements.txt

ORG-025 – Permissive SSL/TLS Verification — src/common/request_infrastructure.py

ORG-049 – Unpinned Dependencies in requirements.txt — requirements.txt

ORG-050 – Use of Pickle for Scrapy's Caching — .scrapy/ (inferred)

ORG-051 – Lack of User-Agent Rotation Strategy — src/settings.py

ORG-052 – Sensitive Information Could Be Logged — src/common/logging.py

ORG-053 – No Resource Limits on File Downloads — Scrapy Spiders

5️⃣ Testing & Global State

ORG-026 – Hardcoded Scrapy Spider Names — src/orchestrator/pipeline.py

ORG-027 – Monkey-Patching for Tests — src/common/__init__.py

ORG-028 – Global State Objects — src/common/metrics.py, src/common/error_handling.py

ORG-029 – Redundant Code in Data Refresh — src/orchestrator/data_refresh.py

ORG-030 – Missing Implementation Details for URLCache — docs/code_reference.md

ORG-054 – Tests Depend on External Network Access — tests/stage2/test_validator_networking_regression.py

ORG-055 – Test Suite Pollutes Project Directory with Artefacts — pytest.ini (inferred)

ORG-056 – Lack of Property-Based Testing for URL Normalization — tests/common/test_url_canonicalization_regression.py

ORG-057 – Inconsistent Test Naming Conventions — tests/

ORG-058 – Global Metrics Collector Complicates Test Isolation — tests/

6️⃣ Heuristics & Scores

ORG-031 – Ambiguous Confidence Score Implementation — docs/stage1_master_plan.md

ORG-032 – Lack of Feature Flags for Heuristics — docs/stage1_master_plan.md

ORG-059 – Heuristics Are Not Extensible via Plugins — src/stage1/discovery_spider.py

ORG-060 – Confidence Scores Are Not Used for Crawl Prioritization — src/stage1/discovery_spider.py

ORG-061 – Pagination Heuristic Is Not Adaptive — src/stage1/discovery_spider.py

ORG-062 – No Feedback Loop from Validation to Heuristics — src/stage1/, src/stage2/

ORG-063 – Hardcoded AJAX/API Endpoint Keywords — src/stage1/discovery_spider.py

7️⃣ Miscellaneous

ORG-033 – Missing __init__.py in data/samples — docs/pipeline_improvement_plan.md

ORG-064 – Inconsistent Commenting Style and Quality — All modules

ORG-065 – Lack of a `main` Guard in Scripts — src/stage2/validator.py

ORG-066 – Missing Docstrings for Public Functions/Classes — Multiple modules

ORG-067 – No Contribution Guidelines for Code Style (e.g., Black, isort) — CONTRIBUTING.md (missing)

ORG-068 – Outdated Information in Documentation — README.md, docs/

8️⃣ CI/CD & Developer Experience

ORG-069 – No CI/CD Pipeline for Automated Testing — README.md (inferred)

ORG-070 – Lack of a `pyproject.toml` for Modern Packaging — (inferred)

ORG-071 – No Documented Debugging Strategy for Spiders — (inferred)

ORG-072 – No Alerting Mechanism for Pipeline Failures — (inferred from code)

ORG-073 – Data Lineage Is Not Explicitly Tracked Between Stages — (inferred from schemas)