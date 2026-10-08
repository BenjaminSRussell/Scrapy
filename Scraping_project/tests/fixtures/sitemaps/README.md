Sitemap fixtures for `tests/unit/stage1/test_sitemap_fixtures.py` (#234). All offline.

| File | Purpose |
|---|---|
| `index.xml` | sitemapindex: absolute + relative child, a lastmod, a broken child |
| `pages.xml` | urlset with lastmod/priority/image extension, whitespace, duplicates, a non-numeric `<priority>`, empty/missing `<loc>` |
| `news.xml.gz` | gzipped news-extension urlset (served as a `.gz` file) |
| `broken.xml` | truncated XML (malformed) |
| `no_namespace.xml` | legacy urlset without the sitemaps.org namespace |
| `billion_laughs.xml`, `xxe.xml` | entity-expansion / external-entity attacks (must be rejected) |
| `plain.txt` | plain-text sitemap (one URL per line, junk lines ignored) |
| `error_page.html` | an HTML 404 page served where a sitemap was expected |
