#!/usr/bin/env python3
"""Smoke screenshot of the Control Center Overview tab (#1044).

Not part of the default test suite and not a pixel-diff gate. Requires the
``playwright`` package plus Chromium (``python -m playwright install chromium``)
or a local Chrome (``--channel chrome``).

Writes one PNG. CI uploads it as the ``dashboard-overview-screenshot`` artifact
(``overview.png``). See dashboard/README.md.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
ARTIFACT_NAME = "dashboard-overview-screenshot"
DEFAULT_OUT = DASHBOARD_DIR / "visual" / "overview.png"
# Under 1000 so formatNumber prints the integer, not a K/M suffix.
FIXTURE = b"""\
pipeline_running 1
stage1_urls_discovered_total 128
stage1_urls_queued_total 40
stage2_pages_analyzed_total 96
stage2_quality_docs_total 70
stage2_massive_docs_total 12
stage2_avg_word_count 840
stage3_summaries_created_total 64
stage3_documents_deduplicated_total 8
stage4_large_doc_summaries_total 12
stage4_avg_compression_ratio 0.5
pipeline_redis_keys 12
pipeline_redis_memory_bytes 1048576
pipeline_last_update_timestamp 1700000000
pipeline_scout_instances 2
"""
# Clocks and motion would make two captures of the same CSS differ.
_INIT_JS = """
(() => {
  const apply = () => {
    if (document.getElementById('cc-shot-freeze')) return;
    const style = document.createElement('style');
    style.id = 'cc-shot-freeze';
    style.textContent = '*,*::before,*::after{animation:none !important;transition:none !important;} .topbar,.refresh-bar{position:static !important;}';
    (document.head || document.documentElement).appendChild(style);
  };
  apply();
  document.addEventListener('DOMContentLoaded', apply);
})();
"""
def _freeze_js(*, require_paused: bool) -> str:
    """Page predicate. Freezes clocks only after the sample has landed.

    Timers are cleared once, and only when the refresh button is idle, so a
    poll still in flight is not aborted before it can paint.
    """
    paused = "true" if require_paused else "false"
    return f"""
() => {{
  const discovered = document.getElementById('overview-s1-discovered');
  const panel = document.getElementById('tab-overview');
  const refresh = document.getElementById('manual-refresh');
  const pause = document.getElementById('pause-refresh');
  const idle = refresh && refresh.getAttribute('aria-busy') !== 'true';
  const pausedOk = {paused} ? (pause && pause.getAttribute('aria-pressed') === 'true') : true;
  const ready = !!(discovered && panel && panel.classList.contains('active')
    && discovered.textContent.trim() === '128'
    && !discovered.classList.contains('is-loading')
    && idle && pausedOk);
  if (!ready) return false;
  document.querySelectorAll('.topbar, .refresh-bar').forEach((el) => {{
    el.style.setProperty('position', 'static', 'important');
  }});
  document.querySelectorAll('.activity-timestamp, #last-refresh-time').forEach((el) => {{
    el.textContent = '00:00:00';
  }});
  const rel = document.getElementById('last-updated-rel');
  if (rel) rel.textContent = '0s ago';
  const countdown = document.getElementById('refresh-countdown');
  if (countdown) countdown.textContent = 'paused';
  document.querySelectorAll('.flash').forEach((el) => el.classList.remove('flash'));
  if (!window.__ccShotFrozen) {{
    window.__ccShotFrozen = true;
    const last = window.setTimeout(() => {{}}, 0);
    for (let i = 0; i <= last; i++) {{
      window.clearTimeout(i);
      window.clearInterval(i);
    }}
  }}
  return true;
}}
"""


class _MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(FIXTURE)))
        self.end_headers()
        self.wfile.write(FIXTURE)

    def log_message(self, fmt, *args):
        return


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _tail(logf) -> str:
    logf.seek(0)
    data = logf.read().decode("utf-8", "replace")
    return data[-2000:]


def _wait_http(url: str, proc: subprocess.Popen, logf, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"dashboard server exited {proc.returncode}\n{_tail(logf)}")
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise SystemExit(f"timed out waiting for {url}\n{_tail(logf)}")


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def capture(out: Path, channel: str | None, width: int, height: int, timeout: float) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit(
            "playwright is not installed. This script is outside the default suite: "
            "pip install 'playwright==1.55.0' && python -m playwright install chromium"
        ) from exc

    metrics_port = _free_port()
    dash_port = _free_port()
    metrics_httpd = ThreadingHTTPServer(("127.0.0.1", metrics_port), _MetricsHandler)
    metrics_thread = threading.Thread(target=metrics_httpd.serve_forever, daemon=True)
    metrics_thread.start()

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "METRICS_UPSTREAM": f"http://127.0.0.1:{metrics_port}/metrics",
        "CC_VERSION": "screenshot",
        "CC_HOST": "127.0.0.1",
        "CC_PORT": str(dash_port),
        # Queue depths are not in the Overview shot. Do not use REDIS_URL.
        "REDIS_HOST": "127.0.0.1",
        "REDIS_PORT": "1",
    }
    logf = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        [sys.executable, str(DASHBOARD_DIR / "serve.py"), "--host", "127.0.0.1", "--port", str(dash_port)],
        cwd=str(DASHBOARD_DIR.parent),
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{dash_port}/"
    try:
        _wait_http(url, proc, logf, timeout)
        with sync_playwright() as playwright:
            launch = {"headless": True}
            if channel:
                launch["channel"] = channel
            try:
                browser = playwright.chromium.launch(**launch)
            except Exception as exc:
                raise SystemExit(
                    f"could not launch Chromium ({exc}). "
                    "Install it with `python -m playwright install chromium` "
                    "or pass `--channel chrome`."
                ) from exc
            context = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
                locale="en-US",
                timezone_id="UTC",
            )
            page = context.new_page()
            page.add_init_script(_INIT_JS)
            # Chart.js is a CDN script. Abort it so the offline fallback is stable.
            page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_function(_freeze_js(require_paused=False), timeout=int(timeout * 1000))
            page.click("#pause-refresh")
            page.wait_for_function(_freeze_js(require_paused=True), timeout=int(timeout * 1000))
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out), full_page=True)
            browser.close()
    finally:
        _stop(proc)
        metrics_httpd.shutdown()
        logf.close()

    if not out.is_file() or out.stat().st_size < 8000 or out.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"screenshot missing or not a PNG: {out}")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    print(f"artifact: {ARTIFACT_NAME} file=overview.png selector=#tab-overview")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Capture the Overview tab smoke screenshot (#1044)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"PNG path (default: {DEFAULT_OUT})")
    parser.add_argument(
        "--channel",
        default=os.environ.get("OVERVIEW_SCREENSHOT_CHANNEL", ""),
        help="Playwright browser channel, e.g. chrome. Empty uses bundled Chromium.",
    )
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args(argv)
    capture(Path(args.out), args.channel.strip() or None, args.width, args.height, args.timeout)


if __name__ == "__main__":
    main()
