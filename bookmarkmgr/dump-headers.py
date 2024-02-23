#!/usr/bin/env python

# ruff: noqa: PGH003
# type: ignore

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer


class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(HTTPStatus.OK.value)
        self.end_headers()
        self.wfile.write(b"<script>window.close()</script>")

        print(  # noqa: T201
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
