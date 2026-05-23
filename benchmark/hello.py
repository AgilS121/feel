"""Python HTTP hello server (benchmark)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/hello":
            body = b'"Hello, World"'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a, **k):
        pass

if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", 3004), H)
    print("Python server on :3004")
    srv.serve_forever()
