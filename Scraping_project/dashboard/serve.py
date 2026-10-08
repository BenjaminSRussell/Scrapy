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

import http.server
import socketserver
import os
import subprocess
from pathlib import Path

PORT = 8080
DASHBOARD_DIR = Path(__file__).parent

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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

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

def main():
    print("=" * 80)
    print("🎛️  PIPELINE CONTROL CENTER")
    print("=" * 80)
    print()
    print("📊 Dashboard:  http://localhost:8080")
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

    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        try:
            print(f"✅ Server started on port {PORT}")
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down dashboard server...")
            httpd.shutdown()

if __name__ == "__main__":
    main()
