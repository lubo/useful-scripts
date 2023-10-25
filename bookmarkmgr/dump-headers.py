#!/usr/bin/env python

from http.server import BaseHTTPRequestHandler, HTTPServer


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write("<script>window.close()</script>".encode())

        print(
            tuple(
                (name, value)
                for name, value in self.headers.items()
                if name
                not in {
                    "Cache-Control",
                    "Connection",
                    "Cookie",
                    "Host",
                }
            ),
        )

    def log_message(self, *args, **kwargs):
        pass


with HTTPServer(("localhost", 8080), RequestHandler) as server:
    server.handle_request()
