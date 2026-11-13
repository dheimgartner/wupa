"""Defaults, overridable via CLI options or environment variables."""
import os

DEFAULT_INCLUDE = ("*",)

ENV_BASE_URL = "ODATA_BASE_URL"   # Web API root, e.g. https://host/org/api/data/v9.1


def resolve_base_url(value=None):
    return (value or os.environ.get(ENV_BASE_URL) or "").rstrip("/")
