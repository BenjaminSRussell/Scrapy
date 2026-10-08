"""#25: Stage 1's free-text URL regex must not invent URLs that 404 in Stage 2."""

import pytest
from scrapy.http import HtmlResponse

from src.stage1.processors.url_extractor import URLExtractor

BASE = "https://uconn.edu/dept/page.html"


def _found(fragment: str) -> set[str]:
    resp = HtmlResponse(url=BASE, body=f"<html><body>{fragment}</body></html>".encode(), encoding="utf-8")
    return set(URLExtractor(BASE, ["uconn.edu"]).discover_all_urls(resp))


# Each row: page text -> exactly what main got wrong and what is correct.
CASES = [
    # bare host urljoin'ed as a relative path under the current page
    ("Apply at admissions.uconn.edu/apply now", "https://admissions.uconn.edu/apply",
     "https://uconn.edu/dept/uconn.edu/apply"),
    ("go to www.uconn.edu/news", "https://www.uconn.edu/news", "https://uconn.edu/dept/www.uconn.edu/news"),
    # sentence punctuation kept in the path
    ("Visit https://uconn.edu/admissions.", "https://uconn.edu/admissions", "https://uconn.edu/admissions."),
    # path cut at "~" / ","
    ("See https://uconn.edu/~jdoe/cv for more", "https://uconn.edu/~jdoe/cv", None),
    ("https://uconn.edu/search?q=a,b", "https://uconn.edu/search?q=a,b", "https://uconn.edu/search?q=a"),
    # unbalanced closing paren from prose
    ("(see https://uconn.edu/a/b)", "https://uconn.edu/a/b", "https://uconn.edu/a/b)"),
    # protocol-relative in a comment
    ("<!-- //cdn.uconn.edu/lib.js -->", "https://cdn.uconn.edu/lib.js", None),
]


@pytest.mark.parametrize("text,good,bad", CASES, ids=[c[0][:30] for c in CASES])
def test_free_text_urls_are_real_urls(text, good, bad):
    found = _found(f"<p>{text}</p>")
    assert good in found
    if bad:
        assert bad not in found


@pytest.mark.parametrize(
    "text",
    ["Email jane@uconn.edu today", "Our uconn.education program", "<script>//TODO refactor</script>"],
)
def test_non_urls_generate_nothing(text):
    assert _found(f"<p>{text}</p>") == set()


def test_balanced_parens_in_path_are_kept():
    # parens are not path characters for the regex; the match simply ends before them
    found = _found("<p>https://uconn.edu/wiki/A_(b)</p>")
    assert all(not u.endswith(")") for u in found)


def test_inline_script_and_event_handler_urls_still_found():
    found = _found(
        "<script>var api = 'https://uconn.edu/api/v1/items';</script>"
        "<button onclick=\"location='https://uconn.edu/clicked'\">x</button>"
    )
    assert {"https://uconn.edu/api/v1/items", "https://uconn.edu/clicked"} <= found


def test_templates_still_skipped():
    assert not any("{{" in u or "%7B" in u for u in _found("<p>https://uconn.edu/{{slug}}/x</p>"))
