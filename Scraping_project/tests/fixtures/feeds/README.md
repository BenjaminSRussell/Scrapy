RSS and Atom fixtures for `tests/unit/stage1/test_feed_parser.py` (#643). All offline.

| File | Purpose |
|---|---|
| `sample.rss` | University news RSS: item links, a relative link, a duplicate, a guid-only permalink, a non-permalink guid, an empty item, and an HTML body link that must not be enqueued. The channel `<link>` is the site home, not an item. |
| `sample.atom` | Atom feed: entry alternate link, enclosure skipped, feed-level links skipped, one relative entry link. |
