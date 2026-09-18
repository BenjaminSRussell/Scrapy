#!/usr/bin/env python3

import http.server
import socketserver
import os
from pathlib import Path

script_dir = Path(__file__).parent
os.chdir(script_dir)

PORT = 8080
DASHBOARD_FILE = "pipeline_dashboard.html"

class CORSRequestHandler(http.server.SimpleHTTPRequestHandler):

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        super().end_headers()

    def do_GET(self):
        if self.path == '/':
            self.path = f'/{DASHBOARD_FILE}'
        return super().do_GET()

def main():
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
