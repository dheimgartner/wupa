"""Generate OpenAPI specs from Dynamics 365 / Dataverse OData $metadata."""
from .csdl import Model, parse_metadata
from .openapi import build_spec, select_entity_sets

__all__ = ["Model", "parse_metadata", "build_spec", "select_entity_sets"]
__version__ = "0.1.0"
