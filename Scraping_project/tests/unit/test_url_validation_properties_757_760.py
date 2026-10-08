"""Seeded property tests: URL canonicalization (#760) and validation.py (#757).

No hypothesis dependency: cases come from ``random.Random(SEED)``; a failure
message names the seed and case index, so ``_cases(SEED)[i]`` reproduces it.
"""
from __future__ import annotations

import random
import string
from urllib.parse import parse_qsl, urlparse

import pytest

from src.utils import validation as v
from src.utils.url_canon import TRACKING_PARAMS, canonical_or_raw, canonicalize_url, url_hash

SEED = 760_757
N = 400

HOSTS = ["uconn.edu", "www.uconn.edu", "cs.uconn.edu", "example.com", "news.bbc.co.uk", "münchen.de", "127.0.0.1"]
TRACKING = sorted(TRACKING_PARAMS) + ["utm_x", "UTM_Source"]


def _label(rng, n=6):
    return "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(rng.randint(1, n)))


def _case(rng: random.Random) -> dict:
    scheme = rng.choice(["http", "https"])
    host = rng.choice(HOSTS)
    path = "/" + "/".join(_label(rng) for _ in range(rng.randint(0, 3)))
    params = [(f"k{_label(rng, 3).lower()}", rng.choice(["", _label(rng), "a b", "x/y"])) for _ in range(rng.randint(0, 3))]
    params = list(dict(params).items())  # unique keys
    tracking = [(rng.choice(TRACKING), _label(rng)) for _ in range(rng.randint(0, 2))]
    return {"scheme": scheme, "host": host, "path": path, "params": params, "tracking": tracking,
            "fragment": rng.choice(["", "#top", "#Section-2"])}


def _render(c, *, upper=False, port=False, slash=False, shuffle=None, with_tracking=False, fragment=False):
    from urllib.parse import urlencode

    scheme = c["scheme"].upper() if upper else c["scheme"]
    host = c["host"].upper() if upper else c["host"]
    if port:
        host += ":80" if c["scheme"] == "http" else ":443"
    path = c["path"] + ("/" if slash and not c["path"].endswith("/") else "")
    params = list(c["params"]) + (list(c["tracking"]) if with_tracking else [])
    if shuffle is not None:
        shuffle.shuffle(params)
    query = ("?" + urlencode(params)) if params else ""
    return f"{scheme}://{host}{path}{query}{c['fragment'] if fragment else ''}"


def _cases(seed=SEED, n=N):
    rng = random.Random(seed)
    return [_case(rng) for _ in range(n)]


CASES = _cases()


def test_canonicalize_is_idempotent_and_valid():
    for i, c in enumerate(CASES):
        u = _render(c, upper=True, port=True, slash=True, with_tracking=True, fragment=True)
        once = canonicalize_url(u)
        assert once is not None, f"seed={SEED} case={i}: {u!r}"
        assert canonicalize_url(once) == once, f"seed={SEED} case={i}: not idempotent for {u!r}"
        p = urlparse(once)
        assert v.is_valid_url(once), f"seed={SEED} case={i}"
        assert p.scheme == p.scheme.lower() and p.netloc == p.netloc.lower()
        assert not p.fragment
        assert not p.netloc.endswith((":80", ":443"))
        keys = [k for k, _ in parse_qsl(p.query, keep_blank_values=True)]
        assert keys == sorted(keys), f"seed={SEED} case={i}: params not sorted"
        assert not any(k.lower() in TRACKING_PARAMS or k.lower().startswith("utm_") for k in keys)


def test_equivalent_spellings_share_canonical_form_and_hash():
    shuffler = random.Random(SEED + 1)
    for i, c in enumerate(CASES):
        base = _render(c)
        variants = [
            _render(c, upper=True), _render(c, port=True), _render(c, slash=True),
            _render(c, shuffle=shuffler), _render(c, with_tracking=True), _render(c, fragment=True),
            _render(c, upper=True, port=True, slash=True, shuffle=shuffler, with_tracking=True, fragment=True),
            "  " + base + "\t",
        ]
        want = canonicalize_url(base)
        for u in variants:
            assert canonicalize_url(u) == want, f"seed={SEED} case={i}: {u!r} -> {canonicalize_url(u)!r} != {want!r}"
            assert url_hash(u) == url_hash(base)


def test_distinct_resources_keep_distinct_hashes():
    seen: dict[str, str] = {}
    for c in CASES:
        canon = canonicalize_url(_render(c))
        h = url_hash(canon)
        assert seen.setdefault(h, canon) == canon  # no collisions among distinct canonicals


@pytest.mark.parametrize("bad", ["", "   ", "ftp://uconn.edu/x", "javascript:alert(1)", "mailto:a@uconn.edu",
                                 "//uconn.edu/x", "http://", "uconn.edu/page", None, 42])
def test_non_http_input_is_rejected(bad):
    assert canonicalize_url(bad) is None
    if isinstance(bad, str):
        assert canonical_or_raw(bad) == bad
        assert v.normalize_url(bad) == bad
        assert not v.is_valid_url(bad) or bad.startswith("http")


def test_validation_normalize_url_delegates_to_canonical_form():
    for c in CASES[:100]:
        u = _render(c, upper=True, with_tracking=True, fragment=True)
        assert v.normalize_url(u) == canonicalize_url(u)


# --- validation.py (#757) ----------------------------------------------------------------

def test_is_valid_url_contract():
    for c in CASES:
        assert v.is_valid_url(_render(c))
    assert not v.is_valid_url("https://uconn.edu/" + "a" * 2048)  # length cap
    assert v.is_valid_url("https://uconn.edu/" + "a" * 2000)
    for bad in ("", None, 3, "ftp://uconn.edu", "https:///path", "uconn.edu"):
        assert not v.is_valid_url(bad)


@pytest.mark.parametrize("url,ok", [
    ("https://uconn.edu/", True), ("https://www.uconn.edu/x", True), ("http://CS.UConn.EDU:8080/a", True),
    ("https://uconn.edu./", True),
    ("https://notuconn.edu/", False), ("https://uconn.edu.attacker.example/", False),
    ("https://evil.example/?next=uconn.edu", False), ("https://uconn.edu@evil.example/", False),
    ("https://evil.example/uconn.edu", False), ("ftp://uconn.edu/", False),
])
def test_is_uconn_domain_matches_host_not_substring(url, ok):
    assert v.is_uconn_domain(url) is ok


def test_sanitize_text_properties():
    rng = random.Random(SEED + 2)
    for i in range(N):
        text = "".join(rng.choice(" \t\n\r" + string.ascii_letters + "éß東") for _ in range(rng.randint(0, 60)))
        limit = rng.choice([None, 0, 1, 5, 20, 100])
        out = v.sanitize_text(text, max_length=limit)
        assert out == out.strip() and "  " not in out and "\n" not in out, f"seed={SEED + 2} case={i}"
        assert limit is None or len(out) <= limit, f"seed={SEED + 2} case={i}"
        assert v.sanitize_text(out, max_length=limit) == out  # idempotent
    assert v.sanitize_text("abc", max_length=0) == ""
    assert v.sanitize_text(None) == "" and v.sanitize_text(5) == ""


def test_is_safe_filename_properties():
    rng = random.Random(SEED + 3)
    alphabet = string.ascii_letters + string.digits + "_-."
    for _ in range(N):
        name = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 40)))
        expect = ".." not in name and name != "."
        assert v.is_safe_filename(name) is expect, name
        assert not v.is_safe_filename(name + "/x") and not v.is_safe_filename("x\\" + name)
    for bad in (".", "..", "../etc", "a/b", "a\\b", "x" * 256, "", None, "sp ace", "semi;colon", "nul\x00"):
        assert not v.is_safe_filename(bad)


@pytest.mark.parametrize("domain", ["uconn.edu", "bbc.co.uk", "example.github.io"])
def test_registrable_domain_is_subdomain_invariant_and_idempotent(domain):
    rng = random.Random(SEED + 4)
    for _ in range(50):
        sub = ".".join(_label(rng).lower() for _ in range(rng.randint(1, 3)))
        for form in (f"{sub}.{domain}", f"https://{sub}.{domain.upper()}:8443/p?q=1", f"{sub}.{domain}/x"):
            assert v.registrable_domain(form) == domain, form
    assert v.registrable_domain(v.registrable_domain(f"a.b.{domain}")) == domain
    assert v.registrable_domain("10.1.2.3") == "10.1.2.3" and v.registrable_domain("") == "unknown"


def test_extract_domain_is_lowercase_netloc():
    for c in CASES:
        u = _render(c, upper=True, port=True)
        assert v.extract_domain(u) == urlparse(u).netloc.lower()
    assert v.extract_domain("not a url") == ""
