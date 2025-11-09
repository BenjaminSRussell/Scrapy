#!/usr/bin/env python3
import http.server
import socketserver
import json
from urllib.parse import urlparse, parse_qs

PORT = 8888

MOCK_PAGES = {
    "/": """
        <html>
        <head><title>UConn Home</title></head>
        <body>
            <h1>Welcome to UConn</h1>
            <p>The University of Connecticut is a premier public research university.
            Founded in 1881, UConn has grown to serve over 32,000 students across
            multiple campuses. We offer more than 100 undergraduate majors and over
            80 graduate programs.</p>
            <p>Academic excellence is at the core of our mission. Our faculty members
            are leaders in their fields, conducting groundbreaking research while
            maintaining a commitment to teaching excellence.</p>
        </body>
        </html>
    """,
    "/admissions/": """
        <html>
        <head><title>UConn Admissions</title></head>
        <body>
            <h1>Admissions at UConn</h1>
            <p>We seek students who are academically talented and engaged in their
            communities. The application process includes submitting transcripts,
            test scores, essays, and letters of recommendation.</p>
            <p>We offer both Early Action and Regular Decision application pathways.
            First-year students should have strong academic records with challenging
            coursework. Transfer students are welcomed and should have completed
            college-level work.</p>
        </body>
        </html>
    """,
    "/academics/": """
        <html>
        <head><title>UConn Academics</title></head>
        <body>
            <h1>Academic Programs at UConn</h1>
            <p>UConn offers comprehensive academic programs across all disciplines.
            The College of Liberal Arts and Sciences is our largest school with
            programs in humanities, social sciences, and natural sciences.</p>
            <p>The School of Business offers highly ranked programs in accounting,
            finance, marketing, and management. The School of Engineering provides
            cutting-edge education in mechanical, electrical, computer, and civil
            engineering.</p>
            <p>Additional schools include Education, Nursing, Agriculture, Fine Arts,
            and Pharmacy. Students have access to state-of-the-art facilities,
            research opportunities, and internship programs.</p>
        </body>
        </html>
    """,
    "/research/": """
        <html>
        <head><title>UConn Research</title></head>
        <body>
            <h1>Research at UConn</h1>
            """ + "<p>Research paragraph. " * 5000 + """</p>
            <p>UConn is classified as an R1 research university, the highest
            designation for research activity. Faculty and students conduct
            groundbreaking research in areas including health sciences, engineering,
            agriculture, and social sciences.</p>
        </body>
        </html>
    """,
    "/campus-life/": """
        <html>
        <head><title>UConn Campus Life</title></head>
        <body>
            <h1>Campus Life at UConn</h1>
            <p>Campus life at UConn is vibrant and diverse. With over 600 student
            organizations, Division I athletics, and a strong sense of community,
            students find countless ways to get involved and make lasting connections.</p>
            <p>Student activities include clubs, Greek life, performing arts,
            intramural sports, and volunteer opportunities. The student union serves
            as a hub for campus activities.</p>
        </body>
        </html>
    """,
}

class MockUConnHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in MOCK_PAGES:
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(MOCK_PAGES[path].encode())
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"<html><body><h1>404 Not Found</h1></body></html>")

    def log_message(self, format, *args):
        pass

def main():
    print("=" * 80)
    print("🌐 MOCK UCONN HTTP SERVER")
    print("=" * 80)
    print(f"\n✅ Serving mock UConn pages on port {PORT}")
    print(f"\nAvailable endpoints:")
    for path in MOCK_PAGES.keys():
        print(f"   http://localhost:{PORT}{path}")
    print(f"\n💡 Use this for testing when DNS is unavailable")
    print("   Update URLs to use localhost:{} instead of uconn.edu".format(PORT))
    print("\nPress Ctrl+C to stop the server")
    print("=" * 80 + "\n")

    with socketserver.TCPServer(("", PORT), MockUConnHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n\n🛑 Shutting down mock server...")
            httpd.shutdown()

if __name__ == "__main__":
    main()
