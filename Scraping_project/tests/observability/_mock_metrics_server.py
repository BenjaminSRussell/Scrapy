# Scraping_project/tests/observability/_mock_metrics_server.py
import http.server
import socketserver
import threading
import time

PORT = 8000

MOCK_METRICS = """# HELP kafka_consumer_records_lag Lag of a consumer group at a partition.
# TYPE kafka_consumer_records_lag gauge
kafka_consumer_records_lag{{client_id="consumer-group-a-1",partition="0",topic="topic1"}} 15000
kafka_consumer_records_lag{{client_id="consumer-group-a-2",partition="1",topic="topic1"}} 20000
kafka_consumer_records_lag{{client_id="consumer-group-b-1",partition="0",topic="topic2"}} 5000
# High lag for SLO alert testing
kafka_consumer_records_lag{{client_id="consumer-group-c-1",partition="0",topic="topic3"}} 300000
"""

class MetricsHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/metrics':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(MOCK_METRICS.encode('utf-8'))
        else:
            self.send_response(404)
            self.end_headers()

def run(server_class=socketserver.TCPServer, handler_class=MetricsHandler, port=PORT):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Mock metrics server running on port {port}")
    httpd.serve_forever()

if __name__ == "__main__":
    run()
