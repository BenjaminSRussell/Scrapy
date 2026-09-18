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
from pathlib import Path

PORT = 8080
DASHBOARD_DIR = Path(__file__).parent

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Custom request handler for dashboard files."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        super().end_headers()

    def do_GET(self):
        if self.path == '/':
            self.path = '/index.html'
        return super().do_GET()

def main():
    print("=" * 80)
    print("🎛️  PIPELINE CONTROL CENTER")
    print("=" * 80)
    print()
    print("📊 Dashboard:  http://localhost:8080")
    print("📈 Metrics:    http://localhost:9090/metrics")
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
