#!/usr/bin/env python3
"""
Pipeline Control Center - Dashboard Server

Serves the custom monitoring dashboard on port 8080.
This is completely separate from Grafana (port 3001).

Purpose:
- Real-time operational monitoring
- Pipeline stage visualization
- System health checks
- Activity logging

Grafana Purpose (Different):
- Historical analytics
- Custom queries
- Advanced visualizations
- Alert management
"""

import argparse
import http.server
import socketserver
import os
import subprocess
import sys
from pathlib import Path

# #735: loopback by default. Containers/LAN must opt in explicitly
# (CC_HOST=0.0.0.0 or --host 0.0.0.0), which prints a warning.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
PORT = DEFAULT_PORT  # kept for backwards compatibility
WILDCARD_HOSTS = frozenset({"", "0.0.0.0", "::", "[::]"})
DASHBOARD_DIR = Path(__file__).parent
# #721: only dashboard assets are served; source, tests, docs and dotfiles are 404.
ALLOWED_SUFFIXES = frozenset({".html", ".js", ".css", ".map", ".png", ".svg", ".ico", ".jpg", ".jpeg",
                              ".gif", ".webp", ".woff", ".woff2", ".json", ".txt"})
DENIED_DIRS = frozenset({"tests", "__pycache__", "node_modules"})

# #906 CSP note: report-only for now. index.html still has inline <style>/<script>
# and loads Chart.js from jsDelivr, so an enforcing policy would break the page.
# Violations appear in the browser console. Activity rows are DOM-built
# (textContent), so they need no inline-script allowance. connect-src allows
# http(s) because METRICS_URL is usually <host>:9090 (another origin).
CSP_REPORT_ONLY = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self' http: https:; "
    "img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)

# #906 CSP note: report-only for now. index.html still has inline <style>/<script>
# and loads Chart.js from jsDelivr, so an enforcing policy would break the page.
# Violations appear in the browser console. Activity rows are DOM-built
# (textContent), so they need no inline-script allowance. connect-src allows
# http(s) because METRICS_URL is usually <host>:9090 (another origin).
CSP_REPORT_ONLY = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net; "
    "connect-src 'self' http: https:; "
    "img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler for dashboard files."""

    root: Path = DASHBOARD_DIR
    error_message_format = "<!DOCTYPE html><title>%(code)d</title><p>%(code)d %(message)s</p>\n"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(self.root), **kwargs)

    def list_directory(self, path):  # noqa: ARG002 - no directory listings (#721)
        self.send_error(404, "Not Found")
        return None

    def _is_servable(self, url_path: str) -> bool:
        parts = [p for p in url_path.split("?", 1)[0].split("#", 1)[0].split("/") if p]
        if not parts:
            return True
        if any(p.startswith(".") or p in DENIED_DIRS for p in parts):
            return False
        return Path(parts[-1]).suffix.lower() in ALLOWED_SUFFIXES

    def send_head(self):
        if not self._is_servable(self.path):
            self.send_error(404, "Not Found")
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Content-Security-Policy-Report-Only', CSP_REPORT_ONLY)
        super().end_headers()

    def do_GET(self):
        if self.path == '/':
            self.path = '/index.html'
        if self.path.split('?')[0] == '/version.js':
            version = _cc_version()
            body = f"window.__CC_VERSION__ = {version!r};\n".encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()


def _cc_version() -> str:
    """CC_VERSION env, else `git describe --always --dirty`, else 'dev' (#1041)."""
    env = os.environ.get('CC_VERSION')
    if env:
        return env
    try:
        out = subprocess.run(
            ['git', 'describe', '--always', '--dirty'],
            cwd=str(DASHBOARD_DIR), capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return 'dev'

def resolve_bind(host: str | None = None, port: int | str | None = None, env=None) -> tuple[str, int]:
    """--host/--port, else CC_HOST/CC_PORT, else 127.0.0.1:8080 (#735)."""
    env = os.environ if env is None else env
    host = host if host is not None else env.get("CC_HOST", DEFAULT_HOST)
    raw_port = port if port is not None else env.get("CC_PORT", DEFAULT_PORT)
    try:
        port_num = int(raw_port)
    except (TypeError, ValueError):
        raise SystemExit(f"invalid port: {raw_port!r}")
    if not 0 <= port_num <= 65535:
        raise SystemExit(f"invalid port: {raw_port!r}")
    return str(host), port_num


def make_server(host: str, port: int, handler=DashboardHandler, server_factory=socketserver.TCPServer):
    """Create (but do not start) the server; the factory is injectable for tests."""
    if host in WILDCARD_HOSTS:
        print(f"⚠️  Binding the dashboard to all interfaces ({host or '*'}:{port}); "
              "keep it behind a trusted network.", file=sys.stderr)
    return server_factory((host, port), handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the Pipeline Control Center")
    parser.add_argument("--host", default=None, help=f"bind address (default: CC_HOST or {DEFAULT_HOST})")
    parser.add_argument("--port", default=None, help=f"port (default: CC_PORT or {DEFAULT_PORT})")
    args = parser.parse_args(argv)
    host, port = resolve_bind(args.host, args.port)
    shown = "localhost" if host in ("127.0.0.1", "localhost", "::1") else (host or "0.0.0.0")

    print("=" * 80)
    print("🎛️  PIPELINE CONTROL CENTER")
    print("=" * 80)
    print()
    print(f"📊 Dashboard:  http://{shown}:{port}")
    print("📈 Metrics:    http://<this host>:9090/metrics  (override: ?metrics=<url>)")
    print("📉 Grafana:    http://localhost:3001 (separate analytics)")
    print()
    print("Purpose:")
    print("  - Real-time pipeline monitoring")
    print("  - Stage-by-stage visualization")
    print("  - System health indicators")
    print("  - Activity logging")
    print()
    print("Note: This dashboard is for OPERATIONS, Grafana is for ANALYTICS")
    print()
    print("Press Ctrl+C to stop the server")
    print("=" * 80)
    print()

    with make_server(host, port) as httpd:
        try:
            print(f"✅ Server started on {host or '*'}:{port}")
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down dashboard server...")
            httpd.shutdown()

if __name__ == "__main__":
    main()
