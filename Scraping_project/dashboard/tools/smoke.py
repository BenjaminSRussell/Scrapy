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
import urllib.request
from pathlib import Path

DASH = Path(__file__).resolve().parents[1]
STAGES = ("seeds", "discovery", "analysis", "summaries", "large_docs")


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
    page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
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
    shot("1-pipeline")

    page.locator('.flow-node[data-stage="discovery"]').click()
    h1("Discovery")
    page.wait_for_selector(".bars", timeout=10_000)
    shot("2-stage")

    page.locator("section:last-of-type a.row").first.click()
    page.wait_for_selector(".timeline .step", timeout=10_000)
    if page.locator(".timeline .step").count() != len(STAGES):
        problems.append("URL journey does not list all five stages")
    shot("3-url")
    page.locator("a.back").click()
    h1("Discovery")

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
