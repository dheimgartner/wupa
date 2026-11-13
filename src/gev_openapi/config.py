"""Defaults, overridable via CLI options or environment variables."""
import os

DEFAULT_BASE_URL = "https://gev-int.finma.ch/gev/api/data/v9.1"
DEFAULT_INCLUDE = ("finma_*",)

ENV_BASE_URL = "GEV_BASE_URL"   # Web API root, e.g. https://host/org/api/data/v9.1
ENV_USER = "GEV_USER"           # for --auth ntlm/basic
ENV_PASSWORD = "GEV_PASSWORD"   # for --auth ntlm/basic (prompted if missing)
ENV_TOKEN = "GEV_TOKEN"         # for --auth bearer


def resolve_base_url(value=None):
    return (value or os.environ.get(ENV_BASE_URL) or DEFAULT_BASE_URL).rstrip("/")


def default_auth():
    return "sspi" if os.name == "nt" else "kerberos"
