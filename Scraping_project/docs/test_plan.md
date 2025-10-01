# Future Test Plan

This document outlines a plan for expanding the test suite to improve coverage, resilience, and confidence in the scraping pipeline.

## 1. Configuration System Tests

**Goal:** Ensure the configuration system is robust and predictable.

- **Test YAML Loading:** Verify that `development.yml` and `production.yml` are loaded correctly based on the `--env` flag.
- **Test Environment Overrides:** Write tests to confirm that environment variables (e.g., `SCRAPY_DOWNLOAD_DELAY`) correctly override the values from the YAML files.
- **Test Schema Validation:** Once a formal configuration schema is in place (as proposed in `future_plan.md`), add tests to ensure that invalid configurations (e.g., wrong data types, missing keys) raise appropriate errors.
- **Test `--config-only`:** Create a test to ensure the `--config-only` flag prints the fully resolved configuration without executing any pipeline stages.

## 2. Pipeline Resilience and Error Handling

**Goal:** Verify that the pipeline can handle failures gracefully and resume operations.

- **Test Checkpoint and Resume:**
  - Create an integration test that runs a pipeline stage, stops it midway, and then verifies that resuming the stage continues from the correct checkpoint without reprocessing old data.
  - Test the integrity of checkpoint files.
- **Test Backpressure:** Design a test where a consumer stage is artificially slowed down to verify that the `BatchQueue` correctly applies backpressure and does not grow indefinitely.
- **Test Graceful Shutdown:** Write a test that sends a `SIGINT` signal to the orchestrator and verifies that it shuts down gracefully, completing in-flight work and saving state.
- **Test Network Failure Modes:**
  - Use mocking to simulate various network errors (e.g., DNS failures, connection timeouts, sudden disconnects) during the validation stage and verify the retry logic (exponential backoff, jitter) works as expected.
  - Test the circuit breaker pattern to ensure it trips after a certain number of consecutive failures.

## 3. Browser-Based Discovery (Stage 1)

**Goal:** Ensure the browser-based discovery mechanism is reliable and efficient.

- **Test JavaScript Rendering:** Create tests for pages that are known to render links and content using JavaScript, and verify that the Playwright/Selenium integration correctly discovers these dynamic URLs.
- **Test Resource Limits:** Once implemented, test that the per-site resource limits (e.g., max pages, max time) are respected to prevent runaway browser automation.
- **Test AJAX/XHR Interception:** Write tests to confirm that the browser instrumentation correctly intercepts AJAX/XHR requests and extracts URLs from their responses.

## 4. Data Privacy and Compliance

**Goal:** Verify that privacy-enhancing features are working correctly.

- **Test PII Detection:** Create test cases with sample text containing Personally Identifiable Information (PII) and verify that the privacy checklist correctly flags it.
- **Test Data Redaction:** If automated redaction is implemented, write tests to ensure that it correctly removes or anonymizes sensitive data before it is saved.

## 5. Advanced Feature Tests

**Goal:** Ensure new, advanced features are well-tested.

- **Test Plugin System:**
  - Write tests for the plugin discovery and registration mechanism.
  - Create a sample plugin and write an integration test to ensure it is loaded and executed correctly by the pipeline.
- **Test LLM/VLM Integration:** As AI-powered features are added, develop a testing strategy that may involve:
  - Mocking the model APIs to test the integration logic.
  - Using a small, local model for integration tests.
  - Creating a benchmark set of pages to evaluate the accuracy and reliability of the AI-powered extraction.

## 6. Performance and Benchmarking

**Goal:** Formalize performance testing to track and prevent regressions.

- **Create Benchmark Suite:** Establish a standardized set of tests and a representative data set to benchmark the performance of each pipeline stage.
- **Automate Benchmarking:** Integrate the benchmark suite into the CI/CD pipeline to run on a schedule or on-demand, tracking key metrics like throughput (URLs/sec), memory usage, and CPU load.
- **Regression Alerts:** Set up alerts to notify the team if performance metrics regress beyond a certain threshold after a code change.
