"""Serve Swagger UI for a spec, with an optional local proxy for "Try it out".

Browsers can't call the Dynamics server directly from Swagger UI: it sends no CORS
headers and expects Windows authentication. The proxy forwards /api/* to the real
Web API using the authenticated Python session, and the served spec points its
server URL at /api, so "Try it out" works with your own credentials.
"""
from __future__ import annotations

import copy
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

import requests

CDN = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.20.0"
FORWARD_REQUEST = {"accept", "content-type", "prefer", "if-match", "if-none-match",
                   "odata-version", "odata-maxversion"}
FORWARD_RESPONSE = {"content-type", "odata-entityid", "odata-version", "preference-applied", "etag", "location"}


def _bundle_path() -> Path | None:
    """Local Swagger UI assets from the optional 'swagger-ui-bundle' package."""
    try:
        from swagger_ui_bundle import swagger_ui_path
        return Path(swagger_ui_path)
    except Exception:
        return None


def make_server(spec: dict, host: str = "127.0.0.1", port: int = 8000, upstream: str | None = None,
                session: requests.Session | None = None) -> ThreadingHTTPServer:
    assets = _bundle_path()
    served = copy.deepcopy(spec)
    if upstream:
        if session is None:
            raise ValueError("A session is required for the proxy")
        upstream = upstream.rstrip("/")
        served["servers"] = [{"url": "/api", "description": f"Local proxy to {upstream} (your credentials)"}] \
            + spec.get("servers", [])
        served["security"] = []  # the proxy authenticates; no "Authorize" needed in the UI
    spec_bytes = json.dumps(served, ensure_ascii=False).encode("utf-8")

    base = "/static" if assets else CDN
    page = (resources.files("gev_openapi").joinpath("templates/swagger.html").read_text(encoding="utf-8")
            .replace("__TITLE__", spec.get("info", {}).get("title", "API"))
            .replace("__CSS__", f"{base}/swagger-ui.css")
            .replace("__JS__", f"{base}/swagger-ui-bundle.js")
            .replace("__SPEC__", "/openapi.json")).encode("utf-8")
    lock = threading.Lock()  # auth handshakes (SSPI/NTLM) are per connection; keep calls serial

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _route(self):
            path = urlsplit(self.path).path
            if upstream and (path == "/api" or path.startswith("/api/")):
                return self._proxy()
            if self.command not in ("GET", "HEAD"):
                return self._send(405, b"Method not allowed", "text/plain")
            if path in ("/", "/index.html"):
                return self._send(200, page, "text/html; charset=utf-8")
            if path == "/openapi.json":
                return self._send(200, spec_bytes, "application/json; charset=utf-8")
            if path.startswith("/static/") and assets:
                f = (assets / path[len("/static/"):]).resolve()
                if f.is_file() and assets.resolve() in f.parents:
                    ctype = {".css": "text/css", ".js": "application/javascript",
                             ".png": "image/png", ".map": "application/json"}.get(f.suffix, "application/octet-stream")
                    return self._send(200, f.read_bytes(), ctype, {"Cache-Control": "max-age=3600"})
            return self._send(404, b"Not found", "text/plain")

        def _proxy(self):
            target = upstream + self.path[len("/api"):]
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() in FORWARD_REQUEST or k.lower().startswith("mscrm.")}
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            try:
                with lock:
                    r = session.request(self.command, target, headers=headers, data=body, timeout=120)
            except requests.RequestException as e:
                msg = json.dumps({"error": {"code": "proxy", "message": str(e)}}).encode()
                return self._send(502, msg, "application/json")
            extra = {k: v for k, v in r.headers.items()
                     if k.lower() in FORWARD_RESPONSE and k.lower() != "content-type"}
            self._send(r.status_code, r.content, r.headers.get("Content-Type", "application/octet-stream"), extra)

        do_GET = do_HEAD = do_POST = do_PATCH = do_PUT = do_DELETE = _route

        def log_message(self, fmt, *args):
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    return ThreadingHTTPServer((host, port), Handler)


def serve(spec: dict, host: str = "127.0.0.1", port: int = 8000, upstream: str | None = None,
          session: requests.Session | None = None, open_browser: bool = True):
    server = make_server(spec, host, port, upstream, session)
    url = f"http://{host}:{port}/"
    print(f"Swagger UI on {url}" + (f"  (proxying /api -> {upstream})" if upstream else "") +
          "  - Ctrl+C to stop", file=sys.stderr)
    if not _bundle_path():
        print("  Swagger UI assets load from the jsDelivr CDN; for offline use: "
              "pip install \"gev-openapi[ui]\"", file=sys.stderr)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
