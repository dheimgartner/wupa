"""Download $metadata, and optionally the column metadata $metadata lacks.

$metadata has names and types, but not which columns are required, which can be
set on create/update, their display labels, or the values of choice columns.
Those come from the EntityDefinitions metadata API ("enrichment"), saved as JSON
so spec generation can run offline and repeatably.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

from .http import ODataError, check


def download_metadata(session: requests.Session, base_url: str, out_path: str | Path) -> Path:
    """Save <base_url>/$metadata to out_path (the full document; the server can't filter it)."""
    out = Path(out_path)
    r = check(session.get(f"{base_url}/$metadata", headers={"Accept": "application/xml"},
                          stream=True, timeout=600))
    with out.open("wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 16):
            f.write(chunk)
    return out


def _label(label: dict | None) -> str:
    if not label:
        return ""
    user = label.get("UserLocalizedLabel") or {}
    if user.get("Label"):
        return user["Label"]
    for loc in label.get("LocalizedLabels") or []:
        if loc.get("Label"):
            return loc["Label"]
    return ""


def _get(session, url):
    return check(session.get(url, timeout=120)).json()


# casts for which option values are fetched, and the option-set navigation they expand
_OPTION_CASTS = {
    "PicklistAttributeMetadata": "OptionSet($select=Options),GlobalOptionSet($select=Options)",
    "MultiSelectPicklistAttributeMetadata": "OptionSet($select=Options),GlobalOptionSet($select=Options)",
    "StateAttributeMetadata": "OptionSet($select=Options)",
    "StatusAttributeMetadata": "OptionSet($select=Options)",
}
_LENGTH_CASTS = ("StringAttributeMetadata", "MemoAttributeMetadata")


def fetch_entity_attributes(session: requests.Session, base_url: str, logical_name: str) -> dict:
    """Column metadata for one table, normalised to a compact dict."""
    ed = f"{base_url}/EntityDefinitions(LogicalName='{logical_name}')"
    ent = _get(session, f"{ed}?$select=LogicalName,EntitySetName,PrimaryIdAttribute,PrimaryNameAttribute,DisplayName,Description")
    raw = _get(session, f"{ed}/Attributes?$select=LogicalName,AttributeType,AttributeOf,IsValidForCreate,"
                        "IsValidForUpdate,IsValidForRead,RequiredLevel,DisplayName,Description")["value"]

    attrs = {}
    for a in raw:
        attrs[a["LogicalName"]] = {
            "type": a.get("AttributeType"),
            "attribute_of": a.get("AttributeOf"),
            "create": bool(a.get("IsValidForCreate")),
            "update": bool(a.get("IsValidForUpdate")),
            "read": bool(a.get("IsValidForRead")),
            "required": (a.get("RequiredLevel") or {}).get("Value", "None"),
            "label": _label(a.get("DisplayName")),
            "description": _label(a.get("Description")),
        }

    for cast, expand in _OPTION_CASTS.items():
        try:
            rows = _get(session, f"{ed}/Attributes/Microsoft.Dynamics.CRM.{cast}?$select=LogicalName&$expand={expand}")["value"]
        except ODataError as e:
            print(f"  warning: {logical_name}: {cast} options not loaded ({e})", file=sys.stderr)
            continue
        for row in rows:
            opts = (row.get("OptionSet") or row.get("GlobalOptionSet") or {}).get("Options") or []
            if row["LogicalName"] in attrs:
                attrs[row["LogicalName"]]["options"] = [[o["Value"], _label(o.get("Label"))] for o in opts]
                attrs[row["LogicalName"]]["multi_select"] = cast.startswith("MultiSelect")

    for cast in _LENGTH_CASTS:
        try:
            rows = _get(session, f"{ed}/Attributes/Microsoft.Dynamics.CRM.{cast}?$select=LogicalName,MaxLength")["value"]
        except GevError:
            continue
        for row in rows:
            if row["LogicalName"] in attrs and row.get("MaxLength"):
                attrs[row["LogicalName"]]["max_length"] = row["MaxLength"]

    return {
        "entity_set": ent.get("EntitySetName"),
        "primary_id": ent.get("PrimaryIdAttribute"),
        "primary_name": ent.get("PrimaryNameAttribute"),
        "label": _label(ent.get("DisplayName")),
        "description": _label(ent.get("Description")),
        "attributes": attrs,
    }


def fetch_attributes(session: requests.Session, base_url: str, logical_names, out_path=None,
                     progress=True) -> dict:
    """Enrichment for several tables: {logical_name: {...}}. Failures are reported and skipped."""
    names = sorted(set(logical_names))
    result = {}
    for i, name in enumerate(names, 1):
        if progress:
            print(f"  [{i}/{len(names)}] {name}", file=sys.stderr)
        try:
            result[name] = fetch_entity_attributes(session, base_url, name)
        except ODataError as e:
            print(f"  warning: skipped {name}: {e}", file=sys.stderr)
    if out_path:
        Path(out_path).write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return result


def load_attributes(path: str | Path | None) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))
