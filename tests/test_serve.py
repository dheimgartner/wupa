import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from gev_openapi import build_spec
from gev_openapi.http import make_session
from gev_openapi.serve import make_server


class FakeDataverse(BaseHTTPRequestHandler):
    def _reply(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.dumps({"method": self.command, "path": self.path,
                           "prefer": self.headers.get("Prefer"),
                           "body": self.rfile.read(length).decode() if length else None}).encode()
        self.send_response(201 if self.command == "POST" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("OData-EntityId", "http://up/finma_geschaefts(1)")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PATCH = _reply

    def log_message(self, *a):
        pass


def _start(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}"


def test_ui_spec_and_proxy(model):
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeDataverse)
    up_url = _start(upstream) + "/org/api/data/v9.1"
    spec = build_spec(model, up_url)
    ui = make_server(spec, port=0, upstream=up_url, session=make_session("none"))
    base = _start(ui)
    try:
        assert "SwaggerUIBundle" in requests.get(base + "/").text
        served = requests.get(base + "/openapi.json").json()
        assert served["servers"][0]["url"] == "/api" and served["security"] == []

        r = requests.get(base + "/api/finma_geschaefts?$top=1", headers={"Prefer": "odata.maxpagesize=1"})
        assert r.status_code == 200
        assert r.json()["path"] == "/org/api/data/v9.1/finma_geschaefts?$top=1"
        assert r.json()["prefer"] == "odata.maxpagesize=1"

        r = requests.post(base + "/api/finma_geschaefts", json={"finma_name": "x"})
        assert r.status_code == 201 and r.json()["body"] == '{"finma_name": "x"}'
        assert r.headers["OData-EntityId"].endswith("(1)")
        assert requests.get(base + "/nope").status_code == 404
    finally:
        ui.shutdown(); upstream.shutdown()
