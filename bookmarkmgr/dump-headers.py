#!/usr/bin/env python

from http import HTTPStatus
from http.server import HTTPServer, SimpleHTTPRequestHandler
from typing import override


class RequestHandler(SimpleHTTPRequestHandler):
    @override
    def do_GET(self) -> None:
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

    @override
    def log_message(self, *args: object, **kwargs: object) -> None:
        pass


with HTTPServer(("localhost", 8080), RequestHandler) as server:
    server.handle_request()
