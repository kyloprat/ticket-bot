"""Vercel entry point: Telegram webhook -> relay.handle_webhook."""
import os
import sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "_lib"))

from relay import handle_tick, handle_webhook  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        status = handle_webhook(self.headers.get("X-Telegram-Bot-Api-Secret-Token", ""), raw)
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok" if status == 200 else b"no")

    def do_GET(self):
        tick = parse_qs(urlparse(self.path).query).get("tick", [""])[0]
        status = handle_tick(tick) if tick else 200
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ticket relay up" if not tick else b"ok" if status == 200 else b"no")
