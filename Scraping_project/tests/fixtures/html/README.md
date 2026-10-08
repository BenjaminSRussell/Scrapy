# HTML snapshot fixtures (#273)

Static pages for spider and parser unit tests. Load them with the `html_response`
fixture (`tests/conftest.py`), which returns a Scrapy `HtmlResponse`:

```python
def test_something(html_response):
    response = html_response("simple", url="https://example.com/dept/")
```

| File | Shape |
|---|---|
| `simple.html` | Text plus a link list: relative, absolute, duplicate, fragment, PDF, image, external, `mailto:` and `javascript:` links |
| `js_heavy.html` | SPA shell: empty `#app-root`, bundled scripts and `fetch()` calls, almost no text |
| `empty.html` | Blank shell (no text, no links) |
| `nav_heavy.html` | 30 nav links, a footer of policy links, one content link |

Keep fixtures small, offline (no external assets that need fetching) and deterministic.
When a test needs a new page shape, add a file here instead of an inline string.
