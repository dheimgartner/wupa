"""Parse OData v4 CSDL ($metadata) into a small, typed model."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

EDM_NS = "http://docs.oasis-open.org/odata/ns/edm"
_COLLECTION = re.compile(r"^Collection\((.+)\)$")
_LOOKUP_VALUE = re.compile(r"^_(.+)_value$")


def local_name(qualified: str) -> str:
    """'mscrm.account' / 'Microsoft.Dynamics.CRM.account' -> 'account'; Edm types are kept."""
    m = _COLLECTION.match(qualified)
    if m:
        return f"Collection({local_name(m.group(1))})"
    if qualified.startswith("Edm."):
        return qualified
    return qualified.rsplit(".", 1)[-1]


def lookup_column(ref_property: str) -> str:
    """'_finma_teilprozessid_value' -> 'finma_teilprozessid' (the attribute's logical name)."""
    m = _LOOKUP_VALUE.match(ref_property)
    return m.group(1) if m else ref_property


@dataclass
class Property:
    name: str
    type: str                    # 'Edm.String', 'Collection(Edm.Int32)', or a local complex/enum type name
    nullable: bool = True
    max_length: int | None = None


@dataclass
class NavigationProperty:
    name: str                    # used as '<name>@odata.bind' and in $expand
    target: str                  # local entity type name
    collection: bool
    partner: str | None = None
    ref_property: str | None = None   # e.g. '_finma_teilprozessid_value' for lookups

    @property
    def column(self) -> str | None:
        return lookup_column(self.ref_property) if self.ref_property else None


@dataclass
class StructuredType:
    name: str
    base_type: str | None = None
    abstract: bool = False
    key: list[str] = field(default_factory=list)
    properties: dict[str, Property] = field(default_factory=dict)
    navigation: dict[str, NavigationProperty] = field(default_factory=dict)


@dataclass
class EnumType:
    name: str
    members: list[str] = field(default_factory=list)
    is_flags: bool = False


@dataclass
class Model:
    entity_types: dict[str, StructuredType] = field(default_factory=dict)
    complex_types: dict[str, StructuredType] = field(default_factory=dict)
    enum_types: dict[str, EnumType] = field(default_factory=dict)
    entity_sets: dict[str, str] = field(default_factory=dict)   # set name -> entity type name

    def __hash__(self):  # allow lru_cache on methods
        return id(self)

    # --- inheritance ---------------------------------------------------------
    @lru_cache(maxsize=None)
    def lineage(self, type_name: str) -> tuple[StructuredType, ...]:
        """The type and its ancestors, root first."""
        chain, seen, name = [], set(), type_name
        types = self.entity_types if type_name in self.entity_types else self.complex_types
        while name and name in types and name not in seen:
            seen.add(name)
            t = types[name]
            chain.append(t)
            name = t.base_type
        return tuple(reversed(chain))

    def properties(self, type_name: str) -> dict[str, Property]:
        out: dict[str, Property] = {}
        for t in self.lineage(type_name):
            out.update(t.properties)
        return out

    def navigation(self, type_name: str) -> dict[str, NavigationProperty]:
        out: dict[str, NavigationProperty] = {}
        for t in self.lineage(type_name):
            out.update(t.navigation)
        return out

    def key(self, type_name: str) -> list[str]:
        for t in reversed(self.lineage(type_name)):
            if t.key:
                return t.key
        return []

    def is_a(self, type_name: str, ancestor: str) -> bool:
        return any(t.name == ancestor for t in self.lineage(type_name))

    # --- entity sets ---------------------------------------------------------
    @lru_cache(maxsize=None)
    def sets_for_type(self, type_name: str) -> tuple[str, ...]:
        """Entity sets holding this type; for abstract types (e.g. 'principal'), those of derived types."""
        direct = tuple(s for s, t in self.entity_sets.items() if t == type_name)
        if direct:
            return direct
        derived = [n for n in self.entity_types if n != type_name and self.is_a(n, type_name)]
        return tuple(s for s, t in self.entity_sets.items() if t in derived)


def _structured(el: ET.Element) -> StructuredType:
    t = StructuredType(
        name=el.get("Name"),
        base_type=local_name(el.get("BaseType")) if el.get("BaseType") else None,
        abstract=el.get("Abstract") == "true",
    )
    for child in el:
        tag = child.tag.split("}")[-1]
        if tag == "Key":
            t.key = [ref.get("Name") for ref in child if ref.get("Name")]
        elif tag == "Property":
            ml = child.get("MaxLength")
            t.properties[child.get("Name")] = Property(
                name=child.get("Name"),
                type=local_name(child.get("Type")),
                nullable=child.get("Nullable") != "false",
                max_length=int(ml) if ml and ml.isdigit() else None,
            )
        elif tag == "NavigationProperty":
            raw = child.get("Type")
            m = _COLLECTION.match(raw)
            constraint = child.find(f"{{{EDM_NS}}}ReferentialConstraint")
            t.navigation[child.get("Name")] = NavigationProperty(
                name=child.get("Name"),
                target=local_name(m.group(1) if m else raw),
                collection=bool(m),
                partner=child.get("Partner"),
                ref_property=constraint.get("Property") if constraint is not None else None,
            )
    return t


def parse_metadata(source: str | Path | bytes) -> Model:
    """Parse a $metadata document (file path, or the raw XML as bytes/str)."""
    if isinstance(source, Path) or (isinstance(source, str) and not source.lstrip().startswith("<")):
        root = ET.parse(source).getroot()
    else:
        root = ET.fromstring(source)

    model = Model()
    for schema in root.iter(f"{{{EDM_NS}}}Schema"):
        for el in schema:
            tag = el.tag.split("}")[-1]
            if tag == "EntityType":
                t = _structured(el)
                model.entity_types[t.name] = t
            elif tag == "ComplexType":
                t = _structured(el)
                model.complex_types[t.name] = t
            elif tag == "EnumType":
                model.enum_types[el.get("Name")] = EnumType(
                    name=el.get("Name"),
                    members=[m.get("Name") for m in el if m.tag.endswith("Member")],
                    is_flags=el.get("IsFlags") == "true",
                )
            elif tag == "EntityContainer":
                for es in el:
                    if es.tag.endswith("EntitySet"):
                        model.entity_sets[es.get("Name")] = local_name(es.get("EntityType"))
    return model
