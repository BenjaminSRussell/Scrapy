#!/usr/bin/env python3
"""Serve the Pipeline Dashboard

This script starts a simple HTTP server to serve the pipeline dashboard.
The dashboard will be available at: http://localhost:8080
"""

import http.server
import socketserver
import os
from pathlib import Path

# Change to temp_scripts directory
script_dir = Path(__file__).parent
os.chdir(script_dir)

PORT = 8080
DASHBOARD_FILE = "pipeline_dashboard.html"

class CORSRequestHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP request handler with CORS support"""

    def end_headers(self):
        # Add CORS headers to allow fetching metrics from port 9090
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_GET(self):
        # Serve dashboard as index
        if self.path == '/':
            self.path = f'/{DASHBOARD_FILE}'
        return super().do_GET()

def main():
    """Start the dashboard server"""
    print("=" * 80)
    print("🎨 PIPELINE DASHBOARD SERVER")
    print("=" * 80)
    print(f"\n✅ Serving dashboard on port {PORT}")
    print(f"📊 Dashboard: http://localhost:{PORT}")
    print(f"📈 Metrics:   http://localhost:9090/metrics")
    print(f"\n💡 Open http://localhost:{PORT} in your browser to view the dashboard")
    print("\nPress Ctrl+C to stop the server")
    print("=" * 80 + "\n")

    with socketserver.TCPServer(("", PORT), CORSRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down dashboard server...")
            httpd.shutdown()

if __name__ == "__main__":
    main()
