"""Temporary private health-only process for the first Railway volume import.

Railway file commands require an active deployment. This process never imports
the application, opens a DB, serves files, or starts background jobs.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
import os


class ImportHealth(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/health":
            self.send_error(403)
            return
        body = b'{"status":"importing"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8787"))), ImportHealth).serve_forever()
