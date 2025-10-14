import http.server
import json
import os
import yaml

class MockGrafanaHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"database": "ok", "commit": "mock"}).encode())
        elif self.path == '/api/datasources':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()

            # Read datasources from the YAML file
            datasource_path = os.path.join(os.path.dirname(__file__), '..', '..', 'monitoring', 'grafana_datasource.yml')
            with open(datasource_path, 'r') as f:
                data = yaml.safe_load(f)

            # Add id to each datasource
            for i, ds in enumerate(data['datasources']):
                ds['id'] = i + 1

            self.wfile.write(json.dumps(data['datasources']).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/api/tsdb/query':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            query = json.loads(post_data)

            if query['queries'][0]['expr'] == 'up':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {
                    "results": {
                        "A": {
                            "series": [
                                {
                                    "name": "up",
                                    "points": [[1, 1]]
                                }
                            ]
                        }
                    }
                }
                self.wfile.write(json.dumps(response).encode())
            else:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

def run(server_class=http.server.HTTPServer, handler_class=MockGrafanaHandler, port=33000):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    httpd.serve_forever()

if __name__ == '__main__':
    run()
