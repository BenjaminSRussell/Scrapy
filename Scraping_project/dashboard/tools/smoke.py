#!/usr/bin/env python3
"""Browser smoke test of the Control Center on its demo feed.

Starts ``serve.py --demo`` on a free loopback port, then drives every layer in
Chromium as an iPhone 15 and as a 1280x900 desktop, in light and dark mode:

  Pipeline -> Discovery -> one URL -> Back, Explore drill-down, Activity filter, Health.

Every step asserts what a person would see (headings, rows, the Demo pill) and
the run fails on any console error or uncaught page error. Screenshots of each
layer are written to --out (default: Scraping_project/dashboard/visual/, gitignored).

    python Scraping_project/dashboard/tools/smoke.py            # bundled Chromium
    python Scraping_project/dashboard/tools/smoke.py --channel chrome

Needs ``playwright`` (pinned for the JS spider) and ``python -m playwright install chromium``.
"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

DASH = Path(__file__).resolve().parents[1]
STAGES = ("seeds", "discovery", "analysis", "summaries", "large_docs")


# WCAG contrast of an element's text against the backgrounds it actually sits on.
CONTRAST_JS = """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const parse = (c) => {
    const m = c.match(/rgba?\\(([^)]+)\\)/);
    if (!m) return null;
    const p = m[1].split(/[\\s,\\/]+/).filter(Boolean).map(Number);
    return { r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1 };
  };
  const layers = [];
  for (let n = el; n; n = n.parentElement) {
    const bg = parse(getComputedStyle(n).backgroundColor);
    if (bg && bg.a > 0) layers.push(bg);
    if (bg && bg.a >= 1) break;
  }
  let base = { r: 255, g: 255, b: 255 };
  for (const l of layers.reverse()) base = { r: l.a * l.r + (1 - l.a) * base.r, g: l.a * l.g + (1 - l.a) * base.g, b: l.a * l.b + (1 - l.a) * base.b };
  const f = parse(getComputedStyle(el).color);
  const fg = { r: f.a * f.r + (1 - f.a) * base.r, g: f.a * f.g + (1 - f.a) * base.g, b: f.a * f.b + (1 - f.a) * base.b };
  const lum = (c) => ['r', 'g', 'b'].map((k) => { const v = c[k] / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
  const [hi, lo] = [lum(fg), lum(base)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}"""

# Rendered height of every element matching a selector (CSS px).
HEIGHTS_JS = "(sel) => [...document.querySelectorAll(sel)].filter((e) => e.offsetParent !== null).map((e) => e.getBoundingClientRect().height)"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(DASH / "serve.py"), "--demo", "--demo-seed", "7", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"serve.py exited early:\n{proc.stderr.read()}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/snapshot", timeout=2) as r:
                if r.status == 200:
                    return proc
        except OSError:
            time.sleep(0.3)
    proc.terminate()
    raise SystemExit("serve.py did not answer /api/snapshot within 60 s")


def run_profile(browser, base: str, name: str, context_args: dict, scheme: str, out: Path) -> list[str]:
    problems: list[str] = []
    ctx = browser.new_context(color_scheme=scheme, **context_args)
    page = ctx.new_page()
    # Lookups this test makes on purpose that must 404. Older Chromium (Playwright 1.55, as
    # pinned in CI) logs any 404 fetch as a console error; everything else stays fatal.
    expected_404: list[str] = []

    def on_console(m):
        if m.type != "error":
            return
        url = (m.location or {}).get("url") or ""
        if "404" in m.text and any(url.startswith(prefix) for prefix in expected_404):
            return
        problems.append(f"console: {m.text} ({url[:120] or 'no url'})")

    page.on("console", on_console)
    page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
    tag = f"{name}-{scheme}"

    def shot(view: str) -> None:
        page.wait_for_timeout(700)  # let the push/pop view transition finish
        page.screenshot(path=str(out / f"{tag}-{view}.png"), full_page=False)

    def h1(text: str) -> None:
        page.wait_for_function("t => document.querySelector('h1')?.textContent === t", arg=text, timeout=10_000)

    page.goto(f"{base}/#/pipeline")
    page.wait_for_selector(".flow-node", timeout=15_000)
    nodes = page.locator(".flow-node")
    if nodes.count() != len(STAGES):
        problems.append(f"expected {len(STAGES)} stage cards, saw {nodes.count()}")
    pill = page.locator(".navbar .pill").first.inner_text().strip()
    if pill != "Demo":
        problems.append(f"connection pill says {pill!r}, expected 'Demo'")
    page.wait_for_selector(".chart svg path.line", timeout=10_000)
    # Keyboard: arrows move the segmented selection and the focus together.
    page.get_by_role("radio", name="Discovery").focus()
    page.keyboard.press("ArrowRight")
    page.wait_for_timeout(150)
    focused = page.evaluate("document.activeElement?.textContent")
    if focused != "Analysis":
        problems.append(f"ArrowRight on the throughput control left focus on {focused!r}, not 'Analysis'")
    page.get_by_role("radio", name="Discovery").click()

    # Text contrast at least 4.5:1, including the small tab and sidebar labels.
    for sel in (".tab:not([aria-current])", ".tab[aria-current] span", ".side-item[aria-current]",
                ".flow-sub", ".row-sub", ".section-title"):
        ratio = page.evaluate(CONTRAST_JS, sel)
        if ratio is not None and ratio < 4.5:
            problems.append(f"contrast of {sel} is {ratio:.2f}:1, below 4.5:1")

    if context_args.get("has_touch"):
        # 44 pt touch targets.
        for sel in (".tab", ".segment", ".search input", "a.back", ".icon-btn"):
            small = [h for h in page.evaluate(HEIGHTS_JS, sel) if h < 44]
            if small:
                problems.append(f"{sel} touch target is {min(small):.0f}px tall, below 44")
        # Largest accessibility text size: tab labels must not run into each other.
        page.evaluate("document.documentElement.style.fontSize = '53px'")
        page.wait_for_timeout(200)
        overlap = page.evaluate("""() => {
          const spans = [...document.querySelectorAll('.tab span')].map((s) => s.getBoundingClientRect());
          return spans.some((r, i) => (i && r.left < spans[i - 1].right + 4) || r.right > innerWidth + 0.5);
        }""")
        if overlap:
            problems.append("tab bar labels collide or run off screen at the largest text size")
        page.evaluate("document.documentElement.style.fontSize = ''")
    shot("1-pipeline")

    page.locator('.flow-node[data-stage="discovery"]').click()
    h1("Discovery")
    page.wait_for_selector(".bars", timeout=10_000)
    shot("2-stage")

    page.locator("section:last-of-type a.row").first.click()
    page.wait_for_selector(".timeline .step", timeout=10_000)
    if page.locator(".timeline .step").count() != len(STAGES):
        problems.append("URL journey does not list all five stages")
    ratio = page.evaluate(CONTRAST_JS, ".btn.filled")
    if ratio is not None and ratio < 4.5:
        problems.append(f"contrast of the Open Page button is {ratio:.2f}:1, below 4.5:1")
    shot("3-url")
    page.locator("a.back").click()
    h1("Discovery")

    # A 4 kB URL: the subtitle is clamped (the full path is in its title), not a page-long wall.
    long_url = "https://a.example.edu/" + "segment/" * 500
    expected_404.append(f"{base}/api/url?u=" + urllib.parse.quote("https://a.example.edu/", safe=""))
    page.goto(f"{base}/#/activity/url:" + urllib.parse.quote(long_url, safe=""))
    page.wait_for_selector(".subtitle.url-path", timeout=10_000)
    height = page.evaluate("document.querySelector('.subtitle.url-path').getBoundingClientRect().height")
    titled = page.evaluate("document.querySelector('.subtitle.url-path').title.length")
    if height > 90 or titled < 4000:
        problems.append(f"long URL subtitle is {height:.0f}px tall (title {titled} chars); expected clamped with the full path in title")

    page.goto(f"{base}/#/explore")
    h1("Explore")
    page.wait_for_selector(".section a.row", timeout=10_000)
    first_host = page.locator(".section a.row .row-title").first.inner_text()
    shot("4-explore")
    page.locator(".section a.row").first.click()
    h1(first_host)

    page.goto(f"{base}/#/activity")
    h1("Activity")
    page.wait_for_selector(".section a.row", timeout=10_000)
    all_rows = page.locator(".section .row").count()
    if context_args.get("has_touch"):
        page.locator(".search input").fill("a")  # shows the clear button
        for sel in (".search input", ".search .clear", ".icon-btn"):
            small = [h for h in page.evaluate(HEIGHTS_JS, sel) if h < 44]
            if small:
                problems.append(f"{sel} touch target is {min(small):.0f}px tall, below 44")
        page.locator(".search input").fill("")
    page.get_by_role("radio", name="Errors").click()
    page.wait_for_timeout(300)
    error_rows = page.locator(".section .row").count()
    if error_rows > all_rows:
        problems.append("the Errors filter showed more rows than All")
    shot("5-activity")

    page.goto(f"{base}/#/health")
    h1("Health")
    services = page.locator("section", has_text="Services").locator(".row").count()
    if services < 3:
        problems.append(f"Health lists {services} services, expected 3")
    delta_row = page.locator("section", has_text="Services").locator(".row", has_text="Delta Lake").inner_text()
    if "(simulated)" not in delta_row or "Last commit" not in delta_row:
        problems.append(f"Health's Delta Lake row lacks its path or last commit: {delta_row!r}")
    titled_rows = page.evaluate("[...document.querySelectorAll('.row-title')].filter((t) => t.title === t.textContent).length")
    if not titled_rows:
        problems.append("row titles carry no title attribute with their full text")
    shot("6-health")

    ctx.close()
    return [f"[{tag}] {p}" for p in problems]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=str(DASH / "visual"), help="directory for screenshots")
    parser.add_argument("--channel", default=None, help="browser channel, e.g. chrome (default: bundled Chromium)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    port = free_port()
    server = start_server(port)
    base = f"http://127.0.0.1:{port}"
    problems: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel=args.channel) if args.channel else p.chromium.launch()
            iphone = dict(p.devices["iPhone 15"])
            iphone.pop("default_browser_type", None)
            profiles = {"iphone": iphone, "desktop": {"viewport": {"width": 1280, "height": 900}}}
            for name, ctx_args in profiles.items():
                for scheme in ("light", "dark"):
                    problems += run_profile(browser, base, name, ctx_args, scheme, out)
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    if problems:
        print("Control Center smoke test FAILED:")
        for line in problems:
            print(f"  - {line}")
        return 1
    print(f"Control Center smoke test passed (4 profiles); screenshots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
