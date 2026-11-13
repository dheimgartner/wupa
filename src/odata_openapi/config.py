"""Defaults, overridable via CLI options or environment variables."""
import os

DEFAULT_INCLUDE = ("*",)

ENV_BASE_URL = "ODATA_BASE_URL"   # Web API root, e.g. https://host/org/api/data/v9.1
ENV_USER = "ODATA_USER"           # for --auth ntlm (DOMAIN\username)
ENV_PASSWORD = "ODATA_PASSWORD"   # for --auth ntlm (prompted if missing)


def resolve_base_url(value=None):
    return (value or os.environ.get(ENV_BASE_URL) or "").rstrip("/")
