"""Build an OpenAPI 3.0 document from a parsed CSDL model (Dynamics 365 / Dataverse flavour).

Per selected entity set this produces:
  GET    /{set}            list   (paged, $select/$filter/$orderby/$expand/$top/$count)
  POST   /{set}            create (body: <type>_create)
  GET    /{set}({id})      get
  PATCH  /{set}({id})      update (body: <type>_update)
  DELETE /{set}({id})      delete
and three schemas: <type> (as read), <type>_create and <type>_update. Lookups are
written as '<NavigationProperty>@odata.bind': '/<targetset>(<guid>)'.
"""
from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from .csdl import Model, NavigationProperty

ALL_OPERATIONS = ("list", "get", "create", "update", "delete")

SECURITY_SCHEMES = {
    "negotiate": {"type": "http", "scheme": "negotiate",
                  "description": "Windows integrated authentication (Kerberos/NTLM)."},
    "bearer": {"type": "http", "scheme": "bearer", "description": "OAuth access token (e.g. from ADFS)."},
}

EDM_SCHEMAS = {
    "Edm.String": {"type": "string"},
    "Edm.Guid": {"type": "string", "format": "uuid"},
    "Edm.Boolean": {"type": "boolean"},
    "Edm.Byte": {"type": "integer", "format": "int32", "minimum": 0, "maximum": 255},
    "Edm.SByte": {"type": "integer", "format": "int32"},
    "Edm.Int16": {"type": "integer", "format": "int32"},
    "Edm.Int32": {"type": "integer", "format": "int32"},
    "Edm.Int64": {"type": "integer", "format": "int64"},
    "Edm.Decimal": {"type": "number"},
    "Edm.Double": {"type": "number", "format": "double"},
    "Edm.Single": {"type": "number", "format": "float"},
    "Edm.DateTimeOffset": {"type": "string", "format": "date-time"},
    "Edm.Date": {"type": "string", "format": "date"},
    "Edm.TimeOfDay": {"type": "string", "example": "13:45:00"},
    "Edm.Duration": {"type": "string", "example": "PT1H"},
    "Edm.Binary": {"type": "string", "format": "byte"},
}

# Used only when no attribute metadata (enrichment) is available.
SYSTEM_READONLY_COLUMNS = {"createdon", "modifiedon", "versionnumber"}
SYSTEM_LOOKUPS = {"createdby", "modifiedby", "createdonbehalfby", "modifiedonbehalfby",
                  "owninguser", "owningteam", "owningbusinessunit"}
# Columns Dynamics fills with defaults even when marked required.
DEFAULTED_ON_CREATE = {"ownerid", "statecode", "statuscode"}
REQUIRED_LEVELS = {"SystemRequired", "ApplicationRequired"}

EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def select_entity_sets(model: Model, include=("*",), exclude=()) -> list[str]:
    """Entity set names matching any include pattern and no exclude pattern (fnmatch, case-sensitive)."""
    def hit(name, patterns):
        return any(fnmatch.fnmatchcase(name, p) for p in patterns)
    return sorted(s for s in model.entity_sets if hit(s, include) and not hit(s, exclude or ()))


def _component_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _ref(kind: str, name: str) -> dict:
    return {"$ref": f"#/components/{kind}/{name}"}


@dataclass
class _Lookup:
    nav: NavigationProperty
    column: str
    target_sets: tuple


class SpecBuilder:
    def __init__(self, model: Model, base_url: str, attributes: dict | None = None,
                 operations=ALL_OPERATIONS, security: str = "negotiate", title: str | None = None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.attributes = attributes or {}
        self.operations = set(operations)
        self.security = security
        self.title = title
        self.schemas: dict = {}
        self._pending_types: set = set()

    # --- types ---------------------------------------------------------------
    def type_schema(self, edm_type: str) -> dict:
        m = re.match(r"^Collection\((.+)\)$", edm_type)
        if m:
            return {"type": "array", "items": self.type_schema(m.group(1))}
        if edm_type in EDM_SCHEMAS:
            return dict(EDM_SCHEMAS[edm_type])
        if edm_type in self.model.enum_types or edm_type in self.model.complex_types:
            self._pending_types.add(edm_type)
            return _ref("schemas", _component_name(edm_type))
        return {}  # unknown/abstract: any value

    def _emit_pending_types(self):
        done = set()
        while self._pending_types - done:
            name = (self._pending_types - done).pop()
            done.add(name)
            if name in self.model.enum_types:
                e = self.model.enum_types[name]
                self.schemas[_component_name(name)] = {"type": "string", "enum": e.members}
            else:
                props = {p.name: self.type_schema(p.type) for p in self.model.properties(name).values()}
                self.schemas[_component_name(name)] = {"type": "object", "properties": props}

    # --- helpers -------------------------------------------------------------
    def _attrs(self, type_name: str) -> dict:
        return (self.attributes.get(type_name) or {}).get("attributes") or {}

    def _lookups(self, type_name: str) -> list[_Lookup]:
        out = []
        for nav in self.model.navigation(type_name).values():
            if nav.collection or not nav.ref_property:
                continue
            targets = self.model.sets_for_type(nav.target)
            if targets:
                out.append(_Lookup(nav, nav.column, targets))
        return out

    @staticmethod
    def _options_text(a: dict) -> str:
        opts = a.get("options") or []
        if not opts:
            return ""
        return "Choices: " + "; ".join(f"{v} = {label}" for v, label in opts)

    @staticmethod
    def _join(*parts) -> str:
        return " ".join(p.strip() for p in parts if p and p.strip())

    # --- schemas -------------------------------------------------------------
    def _read_schema(self, type_name: str, lookups: list[_Lookup]) -> dict:
        attrs = self._attrs(type_name)
        key = set(self.model.key(type_name))
        bind_hint = defaultdict(list)
        for lk in lookups:
            bind_hint[lk.nav.ref_property].append(f"{lk.nav.name}@odata.bind")

        props = {}
        for p in self.model.properties(type_name).values():
            s = self.type_schema(p.type)
            if p.name.startswith("_") and p.name.endswith("_value"):
                col = p.name[1:-6]
                a = attrs.get(col, {})
                hint = f"Lookup GUID (read-only). To set it, use {' or '.join(bind_hint[p.name])}." \
                    if bind_hint[p.name] else "Lookup GUID (read-only)."
                s["description"] = self._join(a.get("label", ""), hint)
                s["readOnly"] = True
            else:
                a = attrs.get(p.name, {})
                desc = self._join(a.get("label", ""), a.get("description", ""), self._options_text(a))
                if desc:
                    s["description"] = desc
                if p.name in key:
                    s["readOnly"] = True
            if p.nullable and "$ref" not in s:
                s["nullable"] = True
            props[p.name] = s
        props["@odata.etag"] = {"type": "string", "readOnly": True,
                                "description": "Record version; use in If-Match for optimistic concurrency."}
        ent = self.attributes.get(type_name) or {}
        schema = {"type": "object", "properties": props, "additionalProperties": True,
                  "description": self._join(ent.get("label", ""), ent.get("description", ""),
                                            "With Prefer: odata.include-annotations=\"*\", each value may be "
                                            "accompanied by '<field>@OData.Community.Display.V1.FormattedValue'.")}
        return schema

    def _write_schema(self, type_name: str, lookups: list[_Lookup], mode: str) -> dict:
        attrs = self._attrs(type_name)
        key = set(self.model.key(type_name))
        props, required = {}, []

        for p in self.model.properties(type_name).values():
            if p.name.startswith("_"):
                continue
            a = attrs.get(p.name)
            if a is not None:
                if not a.get(mode) or a.get("attribute_of"):
                    continue
            elif p.name in SYSTEM_READONLY_COLUMNS or (mode == "update" and p.name in key):
                continue
            s = self.type_schema(p.type)
            a = a or {}
            if a.get("options") and not a.get("multi_select") and s.get("type") == "integer":
                s["enum"] = [v for v, _ in a["options"]]
            max_len = a.get("max_length") or p.max_length
            if max_len and s.get("type") == "string" and "format" not in s:
                s["maxLength"] = max_len
            desc = self._join(a.get("label", ""), a.get("description", ""), self._options_text(a))
            if a.get("multi_select"):
                desc = self._join(desc, "Multi-select: comma-separated values, e.g. \"1,2\".")
            if desc:
                s["description"] = desc
            if mode == "update" and "$ref" not in s and "enum" not in s:
                s["nullable"] = True  # PATCH with null clears a column
            props[p.name] = s
            if (mode == "create" and a.get("required") in REQUIRED_LEVELS
                    and p.name not in DEFAULTED_ON_CREATE and p.name not in key):
                required.append(p.name)

        by_column = defaultdict(list)
        for lk in lookups:
            by_column[lk.column].append(lk)
        for col, lks in by_column.items():
            a = attrs.get(col)
            if a is not None:
                if not a.get(mode):
                    continue
            elif lks[0].nav.name in SYSTEM_LOOKUPS:
                continue
            a = a or {}
            for lk in lks:
                prop = f"{lk.nav.name}@odata.bind"
                example = f"/{lk.target_sets[0]}({EMPTY_GUID})"
                desc = self._join(
                    a.get("label", "") + ":" if a.get("label") else "",
                    f"reference to an existing record, as '/{lk.target_sets[0]}(<guid>)'.",
                    f"Allowed targets: {', '.join(lk.target_sets)}." if len(lk.target_sets) > 1 else "",
                    f"Polymorphic lookup '{col}': set only one of {', '.join(x.nav.name + '@odata.bind' for x in lks)}."
                    if len(lks) > 1 else "",
                )
                props[prop] = {"type": "string", "description": desc, "example": example}
                if (mode == "create" and len(lks) == 1 and a.get("required") in REQUIRED_LEVELS
                        and col not in DEFAULTED_ON_CREATE):
                    required.append(prop)

        schema = {"type": "object", "properties": props}
        if required:
            schema["required"] = sorted(set(required))
        return schema

    # --- paths ---------------------------------------------------------------
    def _entity(self, set_name: str, paths: dict, tags: list):
        type_name = self.model.entity_sets[set_name]
        comp = _component_name(type_name)
        is_data = self.model.is_a(type_name, "crmbaseentity")
        lookups = self._lookups(type_name)
        key = self.model.key(type_name)
        ent = self.attributes.get(type_name) or {}

        self.schemas[comp] = self._read_schema(type_name, lookups)
        self.schemas[f"{comp}_collection"] = {
            "type": "object",
            "properties": {
                "@odata.context": {"type": "string"},
                "@odata.count": {"type": "integer", "description": "Total rows (with $count=true; max 5000)."},
                "@odata.nextLink": {"type": "string", "description": "Full URL of the next page; absent on the last page."},
                "value": {"type": "array", "items": _ref("schemas", comp)},
            },
        }
        writable = is_data and len(key) == 1
        if writable and "create" in self.operations:
            self.schemas[f"{comp}_create"] = self._write_schema(type_name, lookups, "create")
        if writable and "update" in self.operations:
            self.schemas[f"{comp}_update"] = self._write_schema(type_name, lookups, "update")

        readable = sorted(self.model.properties(type_name))
        select = {"name": "$select", "in": "query", "style": "form", "explode": False,
                  "description": "Columns to return (fewer = faster). Lookups appear as _<column>_value.",
                  "schema": {"type": "array", "items": {"type": "string", "enum": readable}}}
        navs = sorted(self.model.navigation(type_name))
        expand = {"name": "$expand", "in": "query",
                  "description": "Related records to include, e.g. " +
                                 (f"'{navs[0]}($select=...)'" if navs else "'<navigation>($select=...)'") +
                                 ". Navigation properties: " + ", ".join(navs[:40]) + (" ..." if len(navs) > 40 else ""),
                  "schema": {"type": "string"}}
        label = ent.get("label") or type_name
        tags.append({"name": set_name, "description": f"{label} ({type_name})"})
        tag = [set_name]
        err = {"default": _ref("responses", "Error")}

        coll, item = {}, {}
        if "list" in self.operations:
            coll["get"] = {
                "operationId": f"{set_name}_list", "tags": tag, "summary": f"List {label}",
                "parameters": [select, _ref("parameters", "filter"), _ref("parameters", "orderby"), expand,
                               _ref("parameters", "top"), _ref("parameters", "count"), _ref("parameters", "preferRead")],
                "responses": {"200": {"description": "One page of records.",
                                      "content": {"application/json": {"schema": _ref("schemas", f"{comp}_collection")}}},
                              **err},
            }
        if writable and "create" in self.operations:
            coll["post"] = {
                "operationId": f"{set_name}_create", "tags": tag, "summary": f"Create {label}",
                "parameters": [_ref("parameters", "preferWrite")],
                "requestBody": {"required": True,
                                "content": {"application/json": {"schema": _ref("schemas", f"{comp}_create")}}},
                "responses": {
                    "201": {"description": "Created; record returned (Prefer: return=representation).",
                            "content": {"application/json": {"schema": _ref("schemas", comp)}}},
                    "204": {"description": "Created; the new record's URL is in the OData-EntityId header.",
                            "headers": {"OData-EntityId": _ref("headers", "ODataEntityId")}},
                    **err},
            }
        if len(key) == 1:
            id_param = {"name": "id", "in": "path", "required": True, "description": f"{key[0]} (GUID, no braces)",
                        "schema": {"type": "string", "format": "uuid"}}
            if "get" in self.operations:
                item["get"] = {
                    "operationId": f"{set_name}_get", "tags": tag, "summary": f"Get one {label}",
                    "parameters": [id_param, select, expand, _ref("parameters", "preferRead"),
                                   _ref("parameters", "ifNoneMatch")],
                    "responses": {"200": {"description": "The record.",
                                          "content": {"application/json": {"schema": _ref("schemas", comp)}}},
                                  "304": {"description": "Not modified (If-None-Match)."}, **err},
                }
            if writable and "update" in self.operations:
                item["patch"] = {
                    "operationId": f"{set_name}_update", "tags": tag, "summary": f"Update {label}",
                    "description": "Only the columns sent are changed.",
                    "parameters": [id_param, _ref("parameters", "ifMatch"), _ref("parameters", "preferWrite")],
                    "requestBody": {"required": True,
                                    "content": {"application/json": {"schema": _ref("schemas", f"{comp}_update")}}},
                    "responses": {"204": {"description": "Updated."},
                                  "200": {"description": "Updated; record returned (Prefer: return=representation).",
                                          "content": {"application/json": {"schema": _ref("schemas", comp)}}},
                                  "412": {"description": "Precondition failed (record missing with If-Match: *, or changed since the given ETag)."},
                                  **err},
                }
            if writable and "delete" in self.operations:
                item["delete"] = {
                    "operationId": f"{set_name}_delete", "tags": tag, "summary": f"Delete {label}",
                    "parameters": [id_param], "responses": {"204": {"description": "Deleted."}, **err},
                }
        if coll:
            paths[f"/{set_name}"] = coll
        if item:
            paths[f"/{set_name}({{id}})"] = item

    # --- document ------------------------------------------------------------
    def build(self, entity_sets) -> dict:
        paths = {
            "/WhoAmI": {"get": {
                "operationId": "WhoAmI", "tags": ["_system"], "summary": "Current user (connection test)",
                "responses": {"200": {"description": "Ids of the calling user.", "content": {"application/json": {
                    "schema": {"type": "object", "properties": {
                        "UserId": {"type": "string", "format": "uuid"},
                        "BusinessUnitId": {"type": "string", "format": "uuid"},
                        "OrganizationId": {"type": "string", "format": "uuid"}}}}}},
                    "default": _ref("responses", "Error")}}},
        }
        tags = [{"name": "_system", "description": "Utility functions"}]
        for s in entity_sets:
            self._entity(s, paths, tags)
        self._emit_pending_types()
        self.schemas["ODataError"] = {"type": "object", "properties": {"error": {"type": "object", "properties": {
            "code": {"type": "string"}, "message": {"type": "string"}}}}}

        version = re.search(r"/v(\d+(?:\.\d+)*)$", self.base_url)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        doc = {
            "openapi": "3.0.3",
            "info": {
                "title": self.title or "Dataverse Web API",
                "version": version.group(1) if version else "1.0",
                "description": (
                    f"Generated by gev-openapi on {stamp} from {self.base_url}/$metadata "
                    f"({len(entity_sets)} entity sets"
                    f"{', with column metadata' if self.attributes else ', without column metadata'}).\n\n"
                    "OData v4 conventions: filter with `$filter` (e.g. `statecode eq 0`, lookups via "
                    "`_<column>_value eq <guid>`); follow `@odata.nextLink` for further pages; set lookups "
                    "with `<NavigationProperty>@odata.bind: /<entityset>(<guid>)`."),
            },
            "servers": [{"url": self.base_url}],
            "tags": tags,
            "paths": paths,
            "components": {
                "schemas": self.schemas,
                "parameters": {
                    "filter": {"name": "$filter", "in": "query", "schema": {"type": "string"},
                               "description": "OData filter, e.g. statecode eq 0 and contains(finma_name,'Test')"},
                    "orderby": {"name": "$orderby", "in": "query", "schema": {"type": "string"},
                                "description": "e.g. createdon desc"},
                    "top": {"name": "$top", "in": "query", "schema": {"type": "integer", "minimum": 1},
                            "description": "Maximum number of rows in total."},
                    "count": {"name": "$count", "in": "query", "schema": {"type": "boolean"},
                              "description": "Include @odata.count."},
                    "preferRead": {"name": "Prefer", "in": "header", "schema": {
                        "type": "string", "default": "odata.include-annotations=\"*\",odata.maxpagesize=500"},
                        "description": "Formatted values (labels) and page size."},
                    "preferWrite": {"name": "Prefer", "in": "header", "schema": {
                        "type": "string", "default": "return=representation"},
                        "description": "return=representation returns the written record."},
                    "ifMatch": {"name": "If-Match", "in": "header", "schema": {"type": "string", "default": "*"},
                                "description": "'*' prevents PATCH from creating a missing record (upsert); "
                                               "an ETag gives optimistic concurrency."},
                    "ifNoneMatch": {"name": "If-None-Match", "in": "header", "schema": {"type": "string"},
                                    "description": "ETag; returns 304 if unchanged."},
                },
                "headers": {"ODataEntityId": {"schema": {"type": "string"},
                                              "description": "URL of the created record: .../<entityset>(<guid>)"}},
                "responses": {"Error": {"description": "OData error.", "content": {
                    "application/json": {"schema": _ref("schemas", "ODataError")}}}},
            },
        }
        if self.security in SECURITY_SCHEMES:
            doc["components"]["securitySchemes"] = {self.security: SECURITY_SCHEMES[self.security]}
            doc["security"] = [{self.security: []}]
        return doc


def build_spec(model: Model, base_url: str, include=("finma_*",), exclude=(), attributes=None,
               operations=ALL_OPERATIONS, security="negotiate", title=None) -> dict:
    """Convenience wrapper: select entity sets by pattern and build the OpenAPI document."""
    sets = select_entity_sets(model, include, exclude)
    if not sets:
        raise ValueError(f"No entity sets match include={list(include)} exclude={list(exclude)}")
    return SpecBuilder(model, base_url, attributes, operations, security, title).build(sets)
