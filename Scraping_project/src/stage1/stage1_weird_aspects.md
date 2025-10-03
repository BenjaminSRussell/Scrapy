
I've analyzed the `stage1` code and found several unusual or problematic aspects. Here's a summary of my findings, which I will now save to `stage1_weird_aspects.md`.

### `discovery_pipeline.py`

*   **Hardcoded Configuration:** The output file is hardcoded, making the pipeline inflexible. A `TODO` acknowledges this.
*   **Fragile Hash-Based Deduplication:** The pipeline uses a `.hashes` file for deduplication. The one-time migration logic to create this file could be a single point of failure.
*   **Over-engineered Link Graph:** The integrated `LinkGraphAnalyzer` for PageRank and HITS is complex and generates very verbose logs, which may be overkill for this stage.
*   **Under-developed Item Processing:** The `process_item` method is a `TODO`-marked skeleton that only writes to a file, lacking any advanced logic.
*   **Unprofessional Logging:** The code contains comments like `# more spam logs every 1000 because why not`, which is unprofessional and clutters the logs.

### `discovery_spider.py`

*   **Test Code in Production:** The spider's `__init__` and `_as_iterable` methods explicitly handle `unittest.mock.Mock` objects, blurring the line between testing and production code.
*   **Excessively Complex `__init__`:** The constructor is massive, initializing numerous caches, feedback stores, and a large number of feature flags, making the spider hard to configure and understand.
*   **Convoluted Dynamic Discovery:** The `_discover_dynamic_sources` method is a maze of heuristics, regexes, and a complex throttling system for finding dynamic content, making it difficult to debug.
*   **Redundant Deduplication:** The spider uses both a SQLite-based `URLCache` and in-memory `sets` for URL deduplication, which is redundant.
*   **Messy URL Cleaning:** The `_clean_seed_url` method has extensive logic for fixing malformed URLs, indicating a problem with the quality of the input data.
*   **Incomplete Headless Browser Integration:** The `scrapy-playwright` integration is present but incomplete, with the `_discover_with_headless_browser` method not fully implemented.

In short, the `stage1` code is characterized by a mix of over-engineering and incomplete features, a lack of clear separation between production and test code, and a general need for simplification and cleanup.
