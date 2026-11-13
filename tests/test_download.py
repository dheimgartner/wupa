import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from odata_openapi.cli import main
from odata_openapi.download import fetch_entity_attributes
from odata_openapi.http import make_session

DATA = Path(__file__).parent / "data"
LBL = lambda s: {"UserLocalizedLabel": {"Label": s}, "LocalizedLabels": []}


class FakeMetadataApi(BaseHTTPRequestHandler):
    def do_GET(self):
        p = unquote(self.path)
        if p.endswith("/$metadata"):
            return self._send((DATA / "metadata.xml").read_bytes(), "application/xml")
        if "PicklistAttributeMetadata" in p:
            data = {"value": [{"LogicalName": "finma_prioritaetchoice",
                               "OptionSet": {"Options": [{"Value": 1, "Label": LBL("Hoch")}]}, "GlobalOptionSet": None}]}
        elif "StringAttributeMetadata" in p:
            data = {"value": [{"LogicalName": "finma_name", "MaxLength": 200}]}
        elif "AttributeMetadata" in p:
            data = {"value": []}
        elif "/Attributes" in p:
            data = {"value": [
                {"LogicalName": "finma_name", "AttributeType": "String", "AttributeOf": None, "IsValidForCreate": True,
                 "IsValidForUpdate": True, "IsValidForRead": True, "RequiredLevel": {"Value": "ApplicationRequired"},
                 "DisplayName": LBL("Name"), "Description": LBL("")},
                {"LogicalName": "finma_prioritaetchoice", "AttributeType": "Picklist", "AttributeOf": None,
                 "IsValidForCreate": True, "IsValidForUpdate": True, "IsValidForRead": True,
                 "RequiredLevel": {"Value": "None"}, "DisplayName": LBL("Priorität"), "Description": None}]}
        elif "EntityDefinitions" in p:
            data = {"EntitySetName": "x", "PrimaryIdAttribute": "id", "PrimaryNameAttribute": "finma_name",
                    "DisplayName": LBL("Tabelle"), "Description": None}
        else:
            self.send_error(404); return
        self._send(json.dumps(data).encode(), "application/json")

    def _send(self, body, ctype):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeMetadataApi)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/org/api/data/v9.1"


def test_fetch_entity_attributes():
    srv, url = _server()
    try:
        info = fetch_entity_attributes(make_session("none"), url, "finma_geschaeft")
    finally:
        srv.shutdown()
    a = info["attributes"]
    assert info["label"] == "Tabelle"
    assert a["finma_name"]["required"] == "ApplicationRequired" and a["finma_name"]["max_length"] == 200
    assert a["finma_prioritaetchoice"]["options"] == [[1, "Hoch"]]


def test_cli_build(tmp_path):
    srv, url = _server()
    try:
        rc = main(["build", "--base-url", url, "--auth", "none", "--workdir", str(tmp_path),
                   "-o", str(tmp_path / "openapi.json"), "--enrich", "--include", "finma_geschaefts"])
    finally:
        srv.shutdown()
    assert rc == 0
    spec = json.loads((tmp_path / "openapi.json").read_text(encoding="utf-8"))
    create = spec["components"]["schemas"]["finma_geschaeft_create"]
    assert create["required"] == ["finma_name"]
    assert (tmp_path / "metadata.xml").exists() and (tmp_path / "attributes.json").exists()


def test_cli_generate_offline(tmp_path):
    out = tmp_path / "spec.json"
    assert main(["generate", str(DATA / "metadata.xml"), "-o", str(out),
                 "--attributes", str(DATA / "attributes.json"), "--operations", "list,get,create"]) == 0
    assert "/finma_geschaefts" in json.loads(out.read_text(encoding="utf-8"))["paths"]
